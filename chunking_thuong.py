import re
from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


# -------------------------------------------------------------------------------
# HANG SO CAU HINH
# -------------------------------------------------------------------------------

MIN_SECTION_CHARS       = 60    # Section ngan hon nay -> bo qua, qua ngan de embed
MAX_SECTION_CHARS_TT    = 1200  # Van ban thong tin: section dai hon nay -> tach paragraph
MAX_SECTION_CHARS_HC    = 1200  # Van ban hanh chinh: cho phep section dai hon (giu ngu canh)
PARAGRAPH_CHUNK_OVERLAP = 80


# -------------------------------------------------------------------------------
# REGEX NHAN DIEN CAU TRUC
# -------------------------------------------------------------------------------

# Van ban hanh chinh: dong la tieu de section, ket thuc bang ":"
# Vi du: "Muc dich, yeu cau:", "Noi dung, phuong phap:", "Khoi luong dinh muc:"
# Dieu kien: dong ngan (< 80 ky tu), bat dau bang chu hoa
_RE_SECTION_HANH_CHINH = re.compile(
    r"(?:^|\n)"
    r"("
    r"[A-ZĐÁÀẢÃẠĂẮẶẲẴÂẤẦẨẪẬÊẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴ]"
    r"[^\n]{3,70}"               # noi dung (3-70 ky tu)
    r":"                         # ket thuc bang dau hai cham
    r")"
    r"(?=\s*\n)",                # theo sau la xuong dong
    re.MULTILINE
)

# Van ban thong tin: ALL CAPS header hoac danh so
_RE_SECTION_HEADER = re.compile(
    r"(?:^|\n)"
    r"("
    r"(?:[A-ZĐÁÀẢÃẠĂẮẶẲẴÂẤẦẨẪẬÊẾỀỂỄỆÍÌỈĨỊ"
    r"ÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴ]{4,}[^\n]{0,60})"  # ALL CAPS >= 4 ky tu
    r"|(?:[IVX]+\.\s+[^\n]{5,60})"           # La ma co dau cham: I. II. III.
    r"|(?:\d+\.\s+[A-ZĐÁÀẢÃ][^\n]{5,60})"    # So: 1. Tieu de viet hoa
    r"|(?:Học kỳ\s+[IVXivx\d]+\s*[\(（][^\n]{2,40}[\)）])"  # "Học kỳ IV (17 tín chỉ)"
    r"|(?:Học kỳ\s+[IVXivx\d]+\s*(?=\n))"    # "Học kỳ IV" khong co ngoac
    r")"
    r"(?=\n)",
    re.MULTILINE
)

# Noise can loai bo
_NOISE_PATTERNS = [
    r"NGÂN HÀNG NHÀ NƯỚC VIỆT NAM.*?(?=\n[A-Z]|\Z)",
    r"CỘNG HOÀ XÃ HỘI CHỦ NGHĨA VIỆT NAM.*?Hạnh phúc",
    r"\*\*\s*\*\*",
    r"\\$",
    r"^\s*-{3,}\s*$",
    r"^\s*_{3,}\s*$",
]


# -------------------------------------------------------------------------------
# TIEN XU LY
# -------------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    for p in _NOISE_PATTERNS:
        text = re.sub(p, "", text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _extract_title(text: str, source_path: str = "") -> str:
    for line in text.split("\n"):
        line = line.strip()
        if len(line) > 10 and not re.match(r"^\d+$", line):
            title = re.sub(r"[*#_]", "", line).strip()
            if len(title) > 5:
                return title
    return Path(source_path).stem.replace("_", " ") if source_path else "Khong ro"


def _extract_metadata(text: str, source_path: str = "", loai: str = "thong_tin") -> dict:
    title = _extract_title(text, source_path)

    # Trich so hieu neu co (vi du: "115/ĐT-HVNH", "Số: 23/TB-HVNH")
    so_hieu_m = re.search(r"Số[:\s]*([\w/\-\.]+)", text[:500])
    so_hieu   = so_hieu_m.group(1).strip() if so_hieu_m else ""

    ngay_m = re.search(
        r"(?:ngày|Ngày)\s+(\d+\s+tháng\s+\d+\s+năm\s+\d{4})"
        r"|(\d{1,2}/\d{1,2}/\d{4})",
        text
    )
    ngay_str = ""
    if ngay_m:
        ngay_str = (ngay_m.group(1) or ngay_m.group(2) or "").strip()

    # Trich keywords quan trong de ho tro filter/rerank trong main.py
    _KW_SIGNALS = [
        "học bổng", "khuyến khích", "kkht",
        "học phí", "rèn luyện", "điểm rèn luyện",
        "cảnh báo", "tốt nghiệp",
        "đăng ký", "tín chỉ", "học phần",
        "ngoại ngữ", "ielts", "toeic",
        "công nghệ thông tin", "cntt",
        "lịch học", "ca học", "tiến độ",
        "chứng chỉ", "điều kiện",
    ]
    text_lower = text.lower()
    keywords   = ", ".join(sig for sig in _KW_SIGNALS if sig in text_lower)

    return {
        "source":       source_path,
        "loai_van_ban": "thuong",
        "kieu_van_ban": loai,
        "ten_van_ban":  title,
        "so_hieu":      so_hieu,           # them so_hieu de filter khop voi phap quy
        "ngay":         ngay_str or "Khong ro",
        "co_quan":      "Hoc vien Ngan hang",
        "keywords":     keywords,          # them keywords de rerank chinh xac hon
    }


# -------------------------------------------------------------------------------
# PHAN LOAI KIEU VAN BAN THUONG
# -------------------------------------------------------------------------------

def _detect_kieu(text: str) -> str:
    """
    Phan biet 2 kieu van ban thuong:

    hanh_chinh -- Co >= 2 dong dang "Ten section:" (ket thuc bang dau :)
                  Vi du: "Muc dich, yeu cau:", "Noi dung, phuong phap:"
                  -> Chunking giu nguyen tung section, khong cat nho

    thong_tin  -- Khong co section ":", dung ALL CAPS hoac danh so
                  Vi du: Lich hoc, bang gio, thong bao ngan
                  -> Chunking tach theo header, cat paragraph neu qua dai
    """
    hc_matches = _RE_SECTION_HANH_CHINH.findall(text)
    # Loc bo cac dong qua ngan (khong phai tieu de that su)
    real_headers = [h for h in hc_matches if len(h.rstrip(":").strip()) >= 5]

    if len(real_headers) >= 2:
        return "hanh_chinh"

    return "thong_tin"


# -------------------------------------------------------------------------------
# TACH SECTIONS
# -------------------------------------------------------------------------------

def _split_by_matches(text: str, matches: list) -> list[tuple[str, str]]:
    """Tach text thanh (header, content) dua tren danh sach regex matches."""
    sections: list[tuple[str, str]] = []

    first_start = matches[0].start()
    if first_start > 0:
        intro = text[:first_start].strip()
        if len(intro) >= MIN_SECTION_CHARS:
            sections.append(("Gioi thieu", intro))

    for i, m in enumerate(matches):
        header  = m.group(1).strip() if m.lastindex and m.group(1) else m.group(0).strip()
        start   = m.end()
        end     = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if len(content) >= MIN_SECTION_CHARS:
            sections.append((header, content))

    return sections if sections else [("", text)]


def _detect_sections(text: str, kieu: str) -> list[tuple[str, str]]:
    """
    Tach sections tuy theo kieu van ban:
        hanh_chinh -> tach theo "Tieu de:" (dau hai cham)
        thong_tin  -> tach theo ALL CAPS / numbered header
    """
    if kieu == "hanh_chinh":
        matches = list(_RE_SECTION_HANH_CHINH.finditer(text))
        if len(matches) >= 2:
            return _split_by_matches(text, matches)
        # Fallback: thu ALL CAPS
        matches = list(_RE_SECTION_HEADER.finditer(text))
        if len(matches) >= 2:
            return _split_by_matches(text, matches)
        return [("", text)]

    else:  # thong_tin
        matches = list(_RE_SECTION_HEADER.finditer(text))
        if len(matches) >= 2:
            return _split_by_matches(text, matches)
        return [("", text)]


# -------------------------------------------------------------------------------
# CHUNKING LOGIC
# -------------------------------------------------------------------------------

def _build_context_header(meta: dict, section_title: str) -> str:
    lines = [f"Tai lieu: {meta['ten_van_ban']}"]
    if meta.get("kieu_van_ban") == "hanh_chinh":
        lines.append("Loai: Van ban hanh chinh")
    if section_title:
        lines.append(f"Phan: {section_title}")
    lines.append("-" * 40)
    return "\n".join(lines) + "\n"


def _chunk_one_section(
    header: str,
    content: str,
    meta: dict,
    doc_id: str,
    section_idx: int,
    kieu: str,
) -> list[Document]:
    """
    Chunk 1 section theo kieu van ban:

    hanh_chinh:
        Giu nguyen toan bo section du co dai hon MAX_SECTION_CHARS_HC.
        Ly do: moi section la 1 don vi y nghia hoan chinh.
               Cat ra se lam LLM doc thieu ngu canh.

    thong_tin:
        Section ngan -> giu nguyen.
        Section dai  -> tach paragraph de dam bao vua context window.
    """
    ctx       = _build_context_header(meta, header)
    chunks:  list[Document] = []
    max_chars = MAX_SECTION_CHARS_HC if kieu == "hanh_chinh" else MAX_SECTION_CHARS_TT

    if len(content) <= max_chars or kieu == "hanh_chinh":
        # hanh_chinh: luon giu nguyen du dai bao nhieu
        # thong_tin ngan: giu nguyen
        chunks.append(Document(
            page_content=ctx + content,
            metadata={
                **meta,
                "chunk_id":      f"{doc_id}__s{section_idx}",
                "chunk_type":    "section",
                "section_title": header,
                "section_idx":   str(section_idx),
                "level":         "flat",
                "char_count":    str(len(content)),
            }
        ))
    else:
        # thong_tin dai: tach paragraph
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=MAX_SECTION_CHARS_TT,
            chunk_overlap=PARAGRAPH_CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " "],
        )
        paras = splitter.split_text(content)
        for pi, para in enumerate(paras):
            if len(para.strip()) < MIN_SECTION_CHARS:
                continue
            chunks.append(Document(
                page_content=ctx + para.strip(),
                metadata={
                    **meta,
                    "chunk_id":      f"{doc_id}__s{section_idx}_p{pi + 1}",
                    "chunk_type":    "paragraph",
                    "section_title": header,
                    "section_idx":   str(section_idx),
                    "para_idx":      str(pi + 1),
                    "level":         "flat",
                    "char_count":    str(len(para)),
                }
            ))

    return chunks


# -------------------------------------------------------------------------------
# PUBLIC API -- goi tu vector.py
# -------------------------------------------------------------------------------

def _is_tien_do(text: str, source: str) -> bool:
    """
    Nhận diện file "Tiến độ chương trình đào tạo".
    Đặc điểm: có >= 4 dòng "Học kỳ" và >= 20 dòng "Tín chỉ".
    Những file này nên giữ nguyên toàn bộ thành 1 chunk để
    khi retrieve 1 lần là có đủ tất cả học kỳ.
    """
    hk_count  = len(re.findall(r"Học kỳ\s+[IVXivx\d]+", text))
    tc_count  = len(re.findall(r"Tín chỉ", text))
    ten_file  = Path(source).stem.upper()
    is_tiendo = "TIẾN ĐỘ" in ten_file or "TIEN DO" in ten_file
    return is_tiendo or (hk_count >= 4 and tc_count >= 20)


def chunk_van_ban_thuong(documents: list[Document]) -> list[Document]:
    """
    Entry point: nhan list[Document] -> tra ve list[Document] chunks.

    Tu dong phan loai kieu van ban:
        hanh_chinh -> tach theo "Tieu de:", giu nguyen section
        thong_tin  -> tach theo ALL CAPS header, cat paragraph neu dai
    """
    all_chunks: list[Document] = []

    for doc in documents:
        source = doc.metadata.get("source", "")
        name   = Path(source).name
        text   = _clean_text(doc.page_content)
        doc_id = Path(source).stem.replace(" ", "_")

        # Canh bao neu thuc ra la van ban phap quy
        if re.search(r"Điều\s+\d+[\.:]", text):
            print(f"  [WARN] {name}: phat hien cau truc Dieu/Khoan")
            print(f"         -> Hay dung chunking_NQ.py de co ket qua tot hon")

        # [FIX] File "Tien do chuong trinh": giu nguyen 1 chunk de retrieve 1 lan = du het HK
        if _is_tien_do(text, source):
            meta   = _extract_metadata(text, source, loai="thong_tin")
            doc_id = Path(source).stem.replace(" ", "_")
            ctx    = _build_context_header(meta, "Toan bo tien do chuong trinh")
            chunk  = Document(
                page_content=ctx + text,
                metadata={
                    **meta,
                    "chunk_id":      f"{doc_id}__full",
                    "chunk_type":    "section",
                    "section_title": "Toan bo tien do chuong trinh",
                    "section_idx":   "0",
                    "level":         "flat",
                    "char_count":    str(len(text)),
                }
            )
            print(f"  [OK] {name}: kieu=[tien_do] | 1 chunk toan bo ({len(text)} ky tu)")
            all_chunks.append(chunk)
            continue

        # Phan loai kieu van ban thuong
        kieu = _detect_kieu(text)
        meta = _extract_metadata(text, source, loai=kieu)

        # Tach sections
        sections   = _detect_sections(text, kieu)
        doc_chunks: list[Document] = []

        for si, (header, content) in enumerate(sections):
            sc = _chunk_one_section(header, content, meta, doc_id, si, kieu)
            doc_chunks.extend(sc)

        print(f"  [OK] {name}: kieu=[{kieu}] | {len(sections)} section(s) -> {len(doc_chunks)} chunks")
        all_chunks.extend(doc_chunks)

    print(f"\nTong chunks van ban thuong: {len(all_chunks)}")
    return all_chunks


# -------------------------------------------------------------------------------
# CHAY DOC LAP -- TEST NHANH
# python chunking_thuong.py duong/dan/file.docx
# -------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from langchain_community.document_loaders import Docx2txtLoader

    path   = sys.argv[1] if len(sys.argv) > 1 else "test.docx"
    docs   = Docx2txtLoader(path).load()
    chunks = chunk_van_ban_thuong(docs)

    print(f"\n{'-' * 55}")
    print("Preview tat ca chunks:")
    for i, c in enumerate(chunks):
        t    = c.metadata.get("chunk_type", "?")
        s    = c.metadata.get("section_title", "")
        kieu = c.metadata.get("kieu_van_ban", "?")
        print(f"\n[Chunk {i + 1} | {t} | kieu={kieu} | section: '{s}']")
        print(c.page_content[:400])
        print("-" * 55)