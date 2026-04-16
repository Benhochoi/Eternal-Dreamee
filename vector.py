from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_community.document_loaders import Docx2txtLoader
from langchain_core.documents import Document

import os
import re
import shutil
import zipfile
from pathlib import Path

from chunking_NQ     import chunk_phap_quy
from chunking_thuong import chunk_van_ban_thuong


# -------------------------------------------------------------------------------
# CAU HINH
# -------------------------------------------------------------------------------

EMBEDDINGS      = OllamaEmbeddings(model="nomic-embed-text")
DB_LOCATION     = "./chroma_langchain_db"
WORD_FOLDER     = "./data/processed"
COLLECTION_NAME = "quy_dinh_hvnh"


# -------------------------------------------------------------------------------
# DOMAIN TAGGING — gan domain vao metadata de filter nhanh hon
# -------------------------------------------------------------------------------

# So hieu / ten van ban → domain
_DOMAIN_SIGNALS: list[tuple[str, str]] = [
    ("2786",             "chuyen_doi_tc"),
    ("309",              "chuyen_doi_tc"),
    ("3337",             "ngoai_ngu_cntt"),
    ("2833",             "hoc_phan"),
    ("335",              "quy_che_dao_tao"),
    ("khuyến khích",     "hoc_bong"),
    ("nckh",             "hoc_bong"),      
    ("nghiên cứu khoa học", "hoc_bong"),    
    ("học phí",          "hoc_phi"),
    ("rèn luyện",        "ren_luyen"),
    ("tốt nghiệp",       "tot_nghiep"),
    ("lịch học",         "lich_hoc"),
    ("tiến độ",          "lich_hoc"),
    ("ca học",           "lich_hoc"),       
    ("tự học",           "lich_hoc"),       
    ("hướng dẫn sinh viên", "lich_hoc"),   
]

def _tag_domain(meta: dict) -> str:
    """
    Suy ra domain từ so_hieu và ten_van_ban.
    Gán vào metadata key 'domain' để filter_docs_by_domain trong main.py
    có thể khớp chính xác hơn bằng metadata thay vì chỉ dựa vào text.
    """
    hay = (
        meta.get("so_hieu",     "").lower() + " " +
        meta.get("ten_van_ban", "").lower() + " " +
        meta.get("section_title","").lower()
    )
    for signal, domain in _DOMAIN_SIGNALS:
        if signal.lower() in hay:
            return domain
    return "other"


# -------------------------------------------------------------------------------
# PHAN LOAI VAN BAN
# -------------------------------------------------------------------------------

_RE_SO_HIEU_LOAI = re.compile(
    r"\d+/"
    r"(QD|QĐ|NQ|TT|TB|CV|HD|KH|BC)"
    r"[-\-]",
    re.IGNORECASE
)

# Ma loai -> nhom
# Mo rong: chi can them 1 dong vao dict nay
_LOAI_MAP: dict[str, str] = {
    "QD": "phap_quy",   # Quyet dinh 
    "NQ": "phap_quy",   # Nghi quyet
    "TT": "phap_quy",   # Thong tu
    "TB": "thuong",     # Thong bao
    "CV": "thuong",     # Cong van
    "HD": "thuong",     # Huong dan
    "KH": "thuong",     # Ke hoach
    "BC": "thuong",     # Bao cao
}


def _extract_so_hieu(text: str) -> str:
    """Trich so hieu tu 500 ky tu dau van ban. Vi du: '3337/QD-HVNH'
    """
    m = re.search(r"S\u1ed1[:\s]*([\w]+/[\w\-\.]+)", text[:500])
    return m.group(1).strip() if m else ""


def classify_document(doc: Document) -> str:
    text = doc.page_content

    # Buoc 1: Phan loai theo so hieu
    so_hieu = _extract_so_hieu(text)
    if so_hieu:
        m = _RE_SO_HIEU_LOAI.search(so_hieu)
        if m:
            ma_loai = m.group(1).upper().replace("QĐ", "QD")
            label   = _LOAI_MAP.get(ma_loai)
            if label:
                return label

    # Buoc 2: dem Dieu/Khoan trong noi dung
    if len(re.findall(r"Điều\s+\d+[\.:]", text)) >= 2:
        return "phap_quy"

    # Buoc 3: Khong xac dinh duoc -> thuong
    return "thuong"


def route_documents(
    documents: list[Document],
) -> tuple[list[Document], list[Document]]:

    phap_quy_docs: list[Document] = []
    thuong_docs:   list[Document] = []

    print("\nPhan loai van ban:")
    print(f"  {'File':<46} {'So hieu':<18} Ket qua")
    print(f"  {'-' * 46} {'-' * 18} {'-' * 14}")

    for doc in documents:
        source  = doc.metadata.get("source", "")
        fname   = Path(source).name[:45]
        so_hieu = _extract_so_hieu(doc.page_content)
        label   = classify_document(doc)
        tag     = "[PQ]" if label == "phap_quy" else "[TT]"

        print(f"  {fname:<46} {so_hieu or '(khong co)':<18} {tag} {label}")

        if label == "phap_quy":
            phap_quy_docs.append(doc)
        else:
            thuong_docs.append(doc)

    print(
        f"\n  Tong: [PQ] phap_quy = {len(phap_quy_docs)}"
        f"  |  [TT] thuong = {len(thuong_docs)}"
    )
    return phap_quy_docs, thuong_docs


# -------------------------------------------------------------------------------
# LOAD TAI LIEU
# -------------------------------------------------------------------------------

def load_documents_from_folder(folder_path: str) -> list[Document] | None:

    folder = Path(folder_path)
    print(f"\nDang tai file Word tu: {folder_path}")

    all_files   = list(folder.glob("**/*.docx"))
    valid_files = [f for f in all_files if not f.name.startswith("~")]

    good_files: list[Path]             = []
    bad_files:  list[tuple[Path, str]] = []

    for f in valid_files:
        if f.stat().st_size == 0:
            bad_files.append((f, "file rong (0 bytes)"))
            continue
        try:
            with zipfile.ZipFile(f):
                pass
            good_files.append(f)
        except zipfile.BadZipFile:
            bad_files.append((f, "khong phai docx hop le"))

    if bad_files:
        print(f"  Bo qua {len(bad_files)} file loi:")
        for f, reason in bad_files:
            print(f"    [FAIL] {f.name} -> {reason}")

    if not good_files:
        print(f"  Khong tim thay file .docx hop le nao trong '{folder_path}'")
        return None

    documents: list[Document] = []
    for f in good_files:
        try:
            docs = Docx2txtLoader(str(f)).load()
            documents.extend(docs)
            print(f"  [OK] {f.name}")
        except Exception as e:
            print(f"  [FAIL] {f.name} -> loi khi load: {e}")

    if not documents:
        print("  Khong load duoc noi dung tu file nao!")
        return None

    print(f"\n  Da tai {len(documents)} file")
    return documents


# -------------------------------------------------------------------------------
# CHUNKING -- PHAN LUONG VAO DUNG CHUNKER
# -------------------------------------------------------------------------------

def split_documents(documents: list[Document]) -> list[Document]:

    print("\nDang chunking...")
    phap_quy_docs, thuong_docs = route_documents(documents)

    all_chunks: list[Document] = []

    if phap_quy_docs:
        print("\n  [Phap quy] Hierarchical chunking (Dieu -> Khoan):")
        all_chunks.extend(chunk_phap_quy(phap_quy_docs))

    if thuong_docs:
        print("\n  [Thuong] Section/Paragraph chunking:")
        all_chunks.extend(chunk_van_ban_thuong(thuong_docs))

    # Gan domain vao tung chunk de filter nhanh hon trong main.py
    for c in all_chunks:
        c.metadata["domain"] = _tag_domain(c.metadata)

    # Thong ke theo chunk_type
    type_counts: dict[str, int] = {}
    for c in all_chunks:
        t = c.metadata.get("chunk_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print(f"\n{'-' * 50}")
    print(f"TONG: {len(all_chunks)} chunks")
    labels = {
        "dieu":      "[dieu]   ",
        "khoan":     "  |--[khoan]",
        "section":   "[section]",
        "paragraph": "  |--[para] ",
    }
    for t, n in sorted(type_counts.items()):
        print(f"  {labels.get(t, t)}: {n}")

    return all_chunks


# -------------------------------------------------------------------------------
# VECTOR STORE
# -------------------------------------------------------------------------------

def delete_database() -> bool:
    """Xoa hoan toan vector store cu."""
    if os.path.exists(DB_LOCATION):
        try:
            shutil.rmtree(DB_LOCATION)
            print(f"  Da xoa database: {DB_LOCATION}")
            return True
        except Exception as e:
            print(f"  Loi khi xoa database: {e}")
            return False
    return True


def create_vector_store(
    chunks: list[Document] | None = None,
    reset: bool = False,
) -> Chroma:
    """
    Tao hoac load vector store Chroma.
    reset=True -> xoa database cu truoc khi tao moi.
    """
    if reset:
        print("\n[RESET] Xoa database cu...")
        delete_database()

    print("  Dang khoi tao Vector Store...")
    vector_store = Chroma(
        collection_name=COLLECTION_NAME,
        persist_directory=DB_LOCATION,
        embedding_function=EMBEDDINGS,
    )

    if chunks:
        _add_chunks_dedup(vector_store, chunks)

    return vector_store


def _add_chunks_dedup(vector_store: Chroma, chunks: list[Document]) -> None:
    """
    Add chunks vao vector store, bo qua cac chunk da ton tai (theo chunk_id).
    Tranh duplicate khi chay option [3] nhieu lan voi cung file.
    """
    # Lay tap hop chunk_id hien co trong DB
    try:
        existing = vector_store._collection.get(include=[])
        existing_ids: set[str] = set(existing.get("ids", []))
    except Exception:
        existing_ids = set()

    # Loc ra chunk chua co
    new_chunks = [
        c for c in chunks
        if c.metadata.get("chunk_id", "") not in existing_ids
    ]

    skipped = len(chunks) - len(new_chunks)
    if skipped:
        print(f"  Bo qua {skipped} chunk da ton tai trong DB (chunk_id trung)")

    if not new_chunks:
        print("  Khong co chunk moi de them.")
        return

    print(f"  Dang embed {len(new_chunks)} chunk moi... (co the mat vai phut)")
    batch_size = 50
    for i in range(0, len(new_chunks), batch_size):
        batch = new_chunks[i: i + batch_size]
        vector_store.add_documents(documents=batch)
        done = min(i + batch_size, len(new_chunks))
        print(f"  {done}/{len(new_chunks)}", end="\r")
    print(f"\n  Da luu {len(new_chunks)} chunk moi!")

    return vector_store


def get_database_stats(vector_store: Chroma) -> dict | None:
    """Tra ve thong ke: tong chunks, ten collection, vi tri."""
    try:
        count = vector_store._collection.count()
        return {
            "total_chunks":    count,
            "collection_name": COLLECTION_NAME,
            "location":        DB_LOCATION,
        }
    except Exception as e:
        print(f"  Khong the lay stats: {e}")
        return None


# -------------------------------------------------------------------------------
# SMART RETRIEVER
# -------------------------------------------------------------------------------

def get_smart_retriever(vector_store: Chroma, k: int = 5):
    """
    Smart Retriever -- xu ly ca 2 loai chunk:

    Van ban phap quy (Parent-Child):
        Tim child chunk (khoan) nho -> chinh xac
        Tu dong fetch parent (dieu day du) -> LLM co du context

    Van ban thuong (Flat):
        Tra thang chunk tim duoc (section / paragraph)

    Cach dung:
        retriever = get_smart_retriever(vector_store, k=5)
        docs = retriever.invoke("cau hoi")
    """
    # Tang pool len k*6 de file dung khong bi day ra ngoai top-k
    # Vi du: "CNTT" bi outrank boi "Quy dinh chuan dau ra...cong nghe thong tin"
    base_retriever = vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={"k": k * 6},
    )

    def _exact_name_boost(query: str, docs: list[Document]) -> list[Document]:
        """
        Boost doc co ten_van_ban khop nhieu tu query len dau danh sach.
        Vi du: "tien do CNTT" -> doc ten "TIEN DO...CONG NGHE THONG TIN" duoc keo len
        truoc "Quy dinh chuan dau ra...cong nghe thong tin" du score thap hon.
        Chi xet tu dai >= 4 ky tu de bo stopword ngan.
        """
        q_words = [w for w in re.split(r"\s+", query.lower()) if len(w) >= 4]
        if not q_words:
            return docs

        def name_score(doc: Document) -> float:
            ten = doc.metadata.get("ten_van_ban", "").lower()
            return sum(1 for w in q_words if w in ten) / len(q_words) if ten else 0.0

        boosted = [d for d in docs if name_score(d) >= 0.5]
        rest    = [d for d in docs if name_score(d) < 0.5]
        return boosted + rest

    class SmartRetriever:
        def __init__(self, base, store, top_k):
            self._base  = base
            self._store = store
            self._k     = top_k

        def invoke(self, query: str) -> list[Document]:
            results = self._base.invoke(query)

            # Phan loai: child can fetch parent vs flat doc
            child_parent_ids: list[str]      = []
            flat_docs:        list[Document] = []
            seen_flat:        set[str]       = set()

            for doc in results:
                parent_id = doc.metadata.get("parent_id", "")
                chunk_id  = doc.metadata.get("chunk_id", "")
                if parent_id:
                    if parent_id not in child_parent_ids:
                        child_parent_ids.append(parent_id)
                else:
                    if chunk_id not in seen_flat:
                        seen_flat.add(chunk_id)
                        flat_docs.append(doc)

            # Batch fetch tat ca parent trong 1 lan goi thay vi N lan
            parent_docs: dict[str, Document] = {}
            if child_parent_ids:
                try:
                    fetched = self._store.get(
                        where={"chunk_id": {"$in": child_parent_ids}}
                    )
                    for text, meta in zip(
                        fetched.get("documents", []),
                        fetched.get("metadatas", []),
                    ):
                        cid = (meta or {}).get("chunk_id", "")
                        if cid:
                            parent_docs[cid] = Document(page_content=text, metadata=meta)
                except Exception:
                    for pid in child_parent_ids:
                        try:
                            fetched = self._store.get(where={"chunk_id": pid})
                            if fetched and fetched.get("documents"):
                                parent_docs[pid] = Document(
                                    page_content=fetched["documents"][0],
                                    metadata=fetched["metadatas"][0],
                                )
                        except Exception:
                            pass

            candidates: list[Document] = []
            for pid in child_parent_ids:
                if pid in parent_docs:
                    candidates.append(parent_docs[pid])
            candidates.extend(flat_docs)

            # Boost doc ten khop query len dau truoc khi cat top-k
            candidates = _exact_name_boost(query, candidates)
            return candidates[:self._k]

        def get_relevant_documents(self, query: str) -> list[Document]:
            return self.invoke(query)

    return SmartRetriever(base_retriever, vector_store, k)


# -------------------------------------------------------------------------------
# INTERACTIVE MAIN
# python vector.py
# -------------------------------------------------------------------------------

def main() -> Chroma | None:
    print("=" * 65)
    print("  VECTOR DATABASE MANAGER")
    print("  chunking_NQ.py | chunking_thuong.py")
    print("=" * 65)

    if not os.path.exists(WORD_FOLDER):
        os.makedirs(WORD_FOLDER)
        print(f"\n  Da tao thu muc: {WORD_FOLDER}")
        print("  -> Hay dat file .docx vao thu muc nay roi chay lai!")
        return None

    db_exists = os.path.exists(DB_LOCATION)

    if db_exists:
        temp_store = Chroma(
            collection_name=COLLECTION_NAME,
            persist_directory=DB_LOCATION,
            embedding_function=EMBEDDINGS,
        )
        stats = get_database_stats(temp_store)
        if stats:
            print(f"\n  Database hien tai: {stats['total_chunks']} chunks")
            print(f"  Location: {stats['location']}")

        print("\n" + "=" * 65)
        print("  [1] Dung database hien tai (khong thay doi)")
        print("  [2] Xoa va nap lai toan bo tu dau")
        print("  [3] Them file moi vao database hien tai")
        print("  [0] Thoat")
        print("=" * 65)
        choice = input("\n  Chon (0-3): ").strip()

        if choice == "0":
            print("\n  Tam biet!")
            return None
        elif choice == "1":
            print("\n  Su dung database hien tai.")
            return temp_store
        elif choice == "2":
            docs = load_documents_from_folder(WORD_FOLDER)
            if not docs:
                return None
            chunks = split_documents(docs)
            return create_vector_store(chunks, reset=True)
        elif choice == "3":
            docs = load_documents_from_folder(WORD_FOLDER)
            if not docs:
                return temp_store
            chunks = split_documents(docs)
            _add_chunks_dedup(temp_store, chunks)
            return temp_store
        else:
            print("  [ERROR] Lua chon khong hop le!")
            return None
    else:
        print(f"\n  Database chua ton tai -- dang tao moi...")
        docs = load_documents_from_folder(WORD_FOLDER)
        if not docs:
            return None
        chunks = split_documents(docs)
        return create_vector_store(chunks)


# -------------------------------------------------------------------------------
# AUTO-INIT KHI IMPORT (dung trong chatbot)
# from vector import retriever
# -------------------------------------------------------------------------------

if __name__ != "__main__":
    if os.path.exists(DB_LOCATION):
        print(f"Loading database: {DB_LOCATION}")
        vector_store = Chroma(
            collection_name=COLLECTION_NAME,
            persist_directory=DB_LOCATION,
            embedding_function=EMBEDDINGS,
        )
        stats = get_database_stats(vector_store)
        if stats:
            print(f"  Loaded {stats['total_chunks']} chunks")
    else:
        print("[WARN] Database chua ton tai! Chay: python vector.py")
        vector_store = None

    retriever = get_smart_retriever(vector_store, k=5) if vector_store else None


# -------------------------------------------------------------------------------
# COMMAND LINE
# -------------------------------------------------------------------------------

if __name__ == "__main__":
    vs = main()
    if vs:
        retriever = get_smart_retriever(vs, k=5)
        print("\n" + "=" * 65)
        print("  Smart Retriever san sang!")
        print("  Dung trong chatbot: from vector import retriever")
        print("=" * 65)