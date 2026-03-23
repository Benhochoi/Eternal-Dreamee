import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from langchain_core.documents import Document


# -------------------------------------------------------------------------------
# HANG SO CAU HINH
# -------------------------------------------------------------------------------

# Dieu dai hon nguong nay thi khong tach khoan (tranh mat context bang)
TABLE_THRESHOLD = 3500

# Gioi han ky tu an toan cho embedding model
# nomic-embed-text / bge-m3 toi da ~8192 tokens ~ 6000 ky tu tieng Viet
# Dat 3000 de tru context header (~200 ky tu) va co margin
MAX_EMBED_CHARS = 3000


# -------------------------------------------------------------------------------
# REGEX
# -------------------------------------------------------------------------------

# Nhan dien ranh gioi Dieu -- phan tu goc cua cu truc phap quy
_RE_DIEU = re.compile(
    r"(Điều\s+\d+[\.:].*?)(?=Điều\s+\d+[\.:]|$)",
    re.DOTALL
)

# Khoan so: "1. noi dung", "2. noi dung"
_RE_KHOAN_SO = re.compile(
    r"(?:^|\n)(\d+\.\s.+?)(?=\n\d+\.\s|\n[a-z]\\?\.\s|\Z)",
    re.DOTALL
)

# Khoan chu: "a. noi dung", "b. noi dung" (pandoc xuat ra dang "a\. ...")
_RE_KHOAN_CHU = re.compile(
    r"(?:^|\n)([a-z]\\?\.\s.+?)(?=\n[a-z]\\?\.\s|\n\d+\.\s|\Z)",
    re.DOTALL
)

# Trich so hieu: "So: 3337/QD-HVNH"
_RE_SO_HIEU = re.compile(r"Số[:\s]*([\w/\-]+(?:QĐ|NQ|TB|CV)[^\s]*)")

# Trich ngay ban hanh
_RE_NGAY = re.compile(
    r"(?:ngày|Hà Nội,\s*ngày)\s+(\d+\s+tháng\s+\d+\s+năm\s+\d{4})"
)

# Noise header/footer lap lai can bo truoc khi xu ly
_NOISE_PATTERNS = [
    r"NGÂN HÀNG NHÀ NƯỚC VIỆT NAM\s*\n.*?HỌC VIỆN NGÂN HÀNG\s*\n",
    r"CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\s*\nĐộc lập.*?Hạnh phúc\s*\n",
    r"KT\.\s*GIÁM ĐỐC.*?(?:\n.*?){0,3}(?=Điều|\Z)",
    r"Nơi nhận:.*?Lưu:.*?\n",
    r"\(Đã ký\)",
    r"\*\*\s*\*\*",   # bold rong tu pandoc
    r"\\$",           # dau \ cuoi dong tu pandoc
]


# -------------------------------------------------------------------------------
# TIEN XU LY
# -------------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Loai bo header/footer lap va chuan hoa khoang trang."""
    for p in _NOISE_PATTERNS:
        text = re.sub(p, "", text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bo bold markdown
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_sections(text: str) -> tuple[str, str]:
    """
    Tach van ban phap quy day du thanh 2 phan:
        PHAN 1 -- QUYET DINH: chua metadata (so hieu, ngay, can cu)
                               KHONG chunk, chi dung de trich metadata
        PHAN 2 -- QUY DINH  : noi dung thuc can chunk

    Tra ve: (phan_quyet_dinh, phan_quy_dinh)
    Neu khong tim thay ranh gioi thi tra ve ("", toan bo text)
    """
    # Tim ranh gioi phan header (QUYET DINH / NGHI QUYET) va phan noi dung (QUY DINH / QUY CHE)
    # Pattern 1: "QUY DINH\n..." + Dieu 1 Pham vi → van ban Quyet dinh day du
    m = re.search(
        r"\nQUY ĐỊNH\s*\n.{0,300}?(?:Ban hành kèm|Điều 1\.\s*Phạm vi)",
        text, re.DOTALL
    )
    if m:
        return text[:m.start()].strip(), text[m.start():].strip()

    # Pattern 2: "QUY CHE ..." → Nghi quyet ban hanh quy che (khong co phan QUYET DINH rieng)
    # Toan bo noi dung la phan quy dinh, chi bo header so hieu truoc dong QUY CHE
    m_quychet = re.search(r"(?:^|\n)((?:QUY CHẾ|QUY ĐỊNH)\s+\S)", text, re.MULTILINE)
    if m_quychet:
        return text[:m_quychet.start()].strip(), text[m_quychet.start():].strip()

    # Fallback: tach tai "Dieu 1. Pham vi"
    m2 = re.search(r"(?:^|\n)(Điều 1\.\s*Phạm vi)", text, re.MULTILINE)
    if m2:
        return text[:m2.start()].strip(), text[m2.start():].strip()

    return "", text


# -------------------------------------------------------------------------------
# TRICH XUAT METADATA
# -------------------------------------------------------------------------------

def _read_docx_properties(docx_path: str) -> dict:
    """
    Doc metadata tu docx core properties khi khong tim thay trong body text.
    docProps/core.xml chua: title, created, subject...
    """
    props = {}
    try:
        with zipfile.ZipFile(docx_path) as z:
            if "docProps/core.xml" not in z.namelist():
                return props
            tree = ET.fromstring(z.read("docProps/core.xml"))
            ns = {
                "dc":      "http://purl.org/dc/elements/1.1/",
                "cp":      "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                "dcterms": "http://purl.org/dc/terms/",
            }
            title   = tree.find(".//dc:title", ns)
            created = tree.find(".//dcterms:created", ns)
            subject = tree.find(".//dc:subject", ns)
            if title   is not None and title.text:
                props["title"]   = title.text.strip()
            if subject is not None and subject.text:
                props["subject"] = subject.text.strip()
            if created is not None and created.text:
                dt    = created.text[:10]   # "2023-09-22"
                parts = dt.split("-")
                if len(parts) == 3:
                    props["created"] = f"{parts[2]}/{parts[1]}/{parts[0]}"
    except Exception:
        pass
    return props


def _extract_metadata(text: str, source_path: str = "") -> dict:
    """
    Trich metadata tu body text, fallback sang docx core properties.
    Tra ve dict gan vao moi Document chunk.
    """
    so_hieu_m = _RE_SO_HIEU.search(text)
    ngay_m    = _RE_NGAY.search(text)

    # Ten van ban: thu lan luot cac pattern pho bien
    # "Ve viec ban hanh ..." → Quyet dinh
    # "QUY DINH\n..." → Quyet dinh dang day du
    # "QUY CHE ..." → Nghi quyet, Quyet dinh ban hanh quy che
    # "NGHI QUYET\n..." → Nghi quyet doc lap
    ten_vb_m = re.search(r"Về việc ban hành (.+?)(?:\n|$)", text, re.IGNORECASE)
    if not ten_vb_m:
        ten_vb_m = re.search(r"QUY ĐỊNH\s*\n+(.+?)(?:\n|\\|$)", text, re.IGNORECASE)
    if not ten_vb_m:
        ten_vb_m = re.search(r"(?:QUY CHẾ|QUY ĐỊNH)\s+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if not ten_vb_m:
        ten_vb_m = re.search(r"NGHỊ QUYẾT\s*\n+(.+?)(?:\n|$)", text, re.IGNORECASE)

    ten_van_ban = ""
    if ten_vb_m:
        ten_van_ban = re.sub(r"\s+", " ", ten_vb_m.group(1)).strip().rstrip("\\")

    # Fallback ngay va ten tu docx properties
    ngay_str = ngay_m.group(1).strip() if ngay_m else ""
    if not ngay_str and source_path:
        props    = _read_docx_properties(source_path)
        ngay_str = props.get("created", "")
        if not ten_van_ban:
            ten_van_ban = props.get("title", "") or props.get("subject", "")

    # Fallback ten tu ten file
    if not ten_van_ban:
        ten_van_ban = Path(source_path).stem.replace("_", " ") if source_path else "Khong ro"

    return {
        "source":        source_path,
        "loai_van_ban":  "phap_quy",
        "so_hieu":       so_hieu_m.group(1).strip() if so_hieu_m else Path(source_path).stem,
        "ngay_ban_hanh": ngay_str or "Khong ro",
        "co_quan":       "Hoc vien Ngan hang",
        "ten_van_ban":   ten_van_ban,
        "hieu_luc":      "true",
    }


# -------------------------------------------------------------------------------
# KIEM TRA TINH DAY DU
# -------------------------------------------------------------------------------

def _validate_document(text: str, source_name: str) -> list[str]:
    """
    Kiem tra tinh day du truoc khi chunk.
    Canh bao neu co Dieu bi thieu trong day so.
    """
    warnings    = []
    dieus_found = re.findall(r"Điều\s+(\d+)[\.:]", text)
    if not dieus_found:
        warnings.append(f"[WARN] [{source_name}] Khong tim thay Dieu/Khoan nao!")
        return warnings

    nums     = sorted(set(int(d) for d in dieus_found))
    expected = list(range(nums[0], nums[-1] + 1))
    missing  = [n for n in expected if n not in nums]
    if missing:
        warnings.append(
            f"[WARN] [{source_name}] Thieu Dieu: {missing} "
            f"(tim thay {nums[0]}->{nums[-1]})"
        )
    return warnings


# -------------------------------------------------------------------------------
# CHUNKING LOGIC
# -------------------------------------------------------------------------------

def _build_context_header(meta: dict, dieu_so: str, dieu_title: str) -> str:
    """
    Prepend contextual header vao moi chunk truoc khi embed.
    Giup embedding model hieu chunk nay thuoc van ban nao, Dieu nao.
    """
    return (
        f"Van ban: {meta['ten_van_ban']}\n"
        f"So hieu: {meta['so_hieu']} | Ngay: {meta['ngay_ban_hanh']}\n"
        f"Dieu {dieu_so}: {dieu_title}\n"
        f"{'-' * 40}\n"
    )


def _split_khoan(dieu_text: str) -> list[str]:
    """
    Tach khoan trong 1 Dieu.
    Uu tien khoan so (1. 2. 3.), fallback khoan chu (a. b. c.).
    Bo khoan qua ngan (< 40 ky tu).
    """
    khoans = _RE_KHOAN_SO.findall(dieu_text)
    if khoans and len(khoans) >= 2:
        return [k.strip() for k in khoans if len(k.strip()) >= 40]

    khoans = _RE_KHOAN_CHU.findall(dieu_text)
    if khoans and len(khoans) >= 2:
        return [k.strip() for k in khoans if len(k.strip()) >= 40]

    return []


def _safe_embed_text(full_text: str, max_chars: int = MAX_EMBED_CHARS) -> list[str]:
    """
    Chia text dai thanh sub-chunks vua context window cua embedding model.
    Chien luoc: tach theo doan (\\n\\n), giu overlap 1 doan o ranh gioi.
    Neu 1 doan don van vuot max_chars (vi du bang lon) thi cat cung.
    """
    if len(full_text) <= max_chars:
        return [full_text]

    parts      = full_text.split("\n\n")
    sub_chunks = []
    current    = ""

    for part in parts:
        candidate = (current + "\n\n" + part).strip() if current else part
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                sub_chunks.append(current.strip())
            if len(part) > max_chars:
                # Doan don qua lon -> cat cung voi overlap nho
                for i in range(0, len(part), max_chars - 100):
                    sub_chunks.append(part[i: i + max_chars].strip())
                current = ""
            else:
                current = part

    if current:
        sub_chunks.append(current.strip())

    return sub_chunks if sub_chunks else [full_text[:max_chars]]


def _extract_keywords(text: str) -> str:
    """
    Trich cac cum tu quan trong trong noi dung Dieu de gan vao metadata.
    Muc dich: giup filter_docs_by_domain va simple_rerank trong main.py
    khop chinh xac hon khi user hoi ve "hoc bong", "canh bao", "IELTS"...

    Logic: tim cac cum tu ket thuc bang dau : hoac nam trong danh sach signal,
    lay toi da 10 cum de metadata khong qua lon.
    """
    IMPORTANT_SIGNALS = [
        "học bổng", "khuyến khích", "kkht",
        "học phí", "miễn giảm học phí",
        "rèn luyện", "điểm rèn luyện",
        "cảnh báo", "buộc thôi học",
        "tốt nghiệp", "xét tốt nghiệp",
        "đăng ký", "hủy học phần", "tín chỉ",
        "ngoại ngữ", "ielts", "toeic", "toefl",
        "công nghệ thông tin", "cntt",
        "chuyển đổi", "công nhận kết quả",
        "chứng chỉ", "cfa", "acca", "frm",
        "điều kiện", "tiêu chuẩn", "đối tượng",
    ]
    text_lower = text.lower()
    found = [sig for sig in IMPORTANT_SIGNALS if sig in text_lower]
    return ", ".join(found[:10])


def _split_by_dieu(content: str, doc_meta: dict) -> list[Document]:
    chunks: list[Document] = []

    # Bat dau tu phan QUY DINH, bo qua phan QUYET DINH phia tren
    qd_start = re.search(r"QUY ĐỊNH\b|(?:^|\n)Điều\s+1[\.:]", content, re.MULTILINE)
    body     = content[qd_start.start():] if qd_start else content

    for match in _RE_DIEU.finditer(body):
        dieu_text = match.group(1).strip()
        if len(dieu_text) < 20:
            continue

        hm = re.match(r"Điều\s+(\d+)[\.:]?\s*(.*?)(?:\n|$)", dieu_text)
        if not hm:
            continue

        dieu_so    = hm.group(1)
        dieu_title = hm.group(2).strip().rstrip("\\").strip()
        ctx_header = _build_context_header(doc_meta, dieu_so, dieu_title)
        parent_id  = f"{doc_meta['so_hieu']}__dieu_{dieu_so}"
        has_tbl    = bool(re.search(r"\|.+\|", dieu_text))

        # PARENT CHUNKS
        full_embed_text = ctx_header + dieu_text
        sub_embed_list  = _safe_embed_text(full_embed_text)
        is_big          = len(sub_embed_list) > 1

        for si, sub_text in enumerate(sub_embed_list):
            sub_id = parent_id if not is_big else f"{parent_id}_p{si + 1}"

            # Trich tu khoa quan trong tu noi dung Dieu de ho tro filter/rerank
            _kws = _extract_keywords(dieu_text)

            chunks.append(Document(
                page_content=sub_text,
                metadata={
                    **doc_meta,
                    "chunk_id":   sub_id,
                    "chunk_type": "dieu",
                    "dieu_so":    dieu_so,
                    "dieu_title": dieu_title,
                    "level":      "parent",
                    "parent_id":  "",
                    "char_count": str(len(dieu_text)),
                    "has_table":  str(has_tbl),
                    "sub_total":  str(len(sub_embed_list)),
                    "sub_index":  str(si + 1),
                    # Tang len 4000 ky tu de LLM doc duoc toan bo Dieu dai
                    "full_text":  dieu_text[:4000],
                    # Keywords giup rerank va filter chinh xac hon
                    "keywords":   _kws,
                }
            ))

        # CHILD CHUNKS (Khoan)
        # Bo qua neu Dieu co bang thuc su hoac qua dai
        if has_tbl or len(dieu_text) > TABLE_THRESHOLD:
            continue

        khoans = _split_khoan(dieu_text)
        for j, khoan_text in enumerate(khoans):
            chunks.append(Document(
                page_content=(
                    ctx_header
                    + f"[Khoan {j + 1} cua Dieu {dieu_so}]\n"
                    + khoan_text
                ),
                metadata={
                    **doc_meta,
                    "chunk_id":   f"{parent_id}__khoan_{j + 1}",
                    "chunk_type": "khoan",
                    "dieu_so":    dieu_so,
                    "dieu_title": dieu_title,
                    "khoan_so":   str(j + 1),
                    "level":      "child",
                    "parent_id":  parent_id,
                    "char_count": str(len(khoan_text)),
                    "has_table":  "false",
                }
            ))

    return chunks


# -------------------------------------------------------------------------------
# PUBLIC API -- goi tu vector.py
# -------------------------------------------------------------------------------

def chunk_phap_quy(documents: list[Document]) -> list[Document]:

    all_chunks: list[Document] = []

    for doc in documents:
        source = doc.metadata.get("source", "")
        name   = Path(source).name
        text   = _clean_text(doc.page_content)

        # Tach 2 phan: QUYET DINH (metadata) va QUY DINH (content)
        phan_qd, phan_qr = _split_sections(text)
        if phan_qd:
            print(
                f"  {name}: file day du "
                f"({len(phan_qd)} ky tu QUYET DINH + {len(phan_qr)} ky tu QUY DINH)"
            )

        # Trich metadata tu toan bo text (phan QD co so hieu, ngay ky)
        meta    = _extract_metadata(text, source)
        content = phan_qr if phan_qr else text

        # Validate
        for w in _validate_document(content, name):
            print(w)

        # Kiem tra co Dieu/Khoan khong
        if not re.search(r"Điều\s+\d+[\.:]", content):
            print(f"  [WARN] {name}: khong co Dieu/Khoan -> bo qua")
            print(f"         (Hay dung chunking_thuong.py cho file nay)")
            continue

        # Chunk
        chunks   = _split_by_dieu(content, meta)
        parents  = sum(1 for c in chunks if c.metadata.get("level") == "parent")
        children = sum(1 for c in chunks if c.metadata.get("level") == "child")
        print(f"  [OK] {name}: {parents} Dieu + {children} Khoan = {len(chunks)} chunks")
        all_chunks.extend(chunks)

    print(f"\nTong chunks phap quy: {len(all_chunks)}")
    return all_chunks

if __name__ == "__main__":
    import sys
    from langchain_community.document_loaders import Docx2txtLoader

    path   = sys.argv[1] if len(sys.argv) > 1 else "test.docx"
    docs   = Docx2txtLoader(path).load()
    chunks = chunk_phap_quy(docs)

    print(f"\n{'-' * 55}")
    print("Preview 3 chunks dau:")
    for c in chunks[:3]:
        t  = c.metadata.get("chunk_type", "?")
        d  = c.metadata.get("dieu_so", "?")
        lv = c.metadata.get("level", "?")
        print(f"\n[{t} | Dieu {d} | {lv}]")
        print(c.page_content[:300])
        print("-" * 55)