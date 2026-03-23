import warnings
warnings.filterwarnings("ignore")

import re
import math
import asyncio
import hashlib
from collections import OrderedDict
from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from vector import vector_store, get_smart_retriever
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

try:
    import numpy as np
    try:
        from scipy.sparse import csr_matrix
        _SCIPY_OK = True
    except ImportError:
        _SCIPY_OK = False
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK  = False
    _SCIPY_OK  = False
    print("[WARN] numpy không có — BM25 dùng pure-Python. Cài: pip install numpy scipy")


# ============================================================
# CẤU HÌNH
# ============================================================

LLM_MODEL = "qwen2.5:3b"

# Giữ nguyên số doc và độ dài — không cắt nội dung pháp quy
MAX_CONTEXT_DOCS  = 5   # ← GIỮ NGUYÊN như bản gốc
# Không giới hạn ký tự per doc — giữ full_text đầy đủ

# Chỉ tối ưu: cache retrieval (không ảnh hưởng nội dung)
RETRIEVAL_CACHE_SIZE = 50


# ============================================================
# NORMALIZE QUERY — chuẩn hóa từ đồng nghĩa / viết tắt
# ============================================================

# Bảng ánh xạ: từ thông thường → từ khóa trong tài liệu
_SYNONYMS: dict[str, str] = {
    # Học bổng / KKHT
    "kkht":                    "khuyến khích học tập",
    "học bổng kkht":           "khuyến khích học tập",
    "hb kkht":                 "khuyến khích học tập",
    "hbkkht":                  "khuyến khích học tập",
    "học bổng khuyến khích":   "khuyến khích học tập",
    "điểm khuyến khích":       "điểm rèn luyện khuyến khích",
    # Học phí
    "hp":                      "học phí",
    "đóng tiền học":           "học phí",
    "nộp tiền":                "nộp học phí",
    # Học phần / tín chỉ
    "hp ":                     "học phần ",
    "tc":                      "tín chỉ",
    "đk hp":                   "đăng ký học phần",
    "hủy hp":                  "hủy học phần",
    "dkhp":                    "đăng ký học phần",
    # Rèn luyện
    "rl":                      "rèn luyện",
    "điểm rl":                 "điểm rèn luyện",
    "điểm rll":                "điểm rèn luyện",
    # Cảnh báo / thôi học
    "cbkqht":                  "cảnh báo kết quả học tập",
    "bị cảnh báo":             "cảnh báo kết quả học tập",
    "buộc thôi học":           "buộc thôi học",
    "btk":                     "buộc thôi học",
    # Tiếng Anh / ngoại ngữ
    "anh văn":                 "tiếng anh",
    "nn":                      "ngoại ngữ",
    "ngoại ngữ đầu ra":        "chuẩn đầu ra ngoại ngữ",
    "chứng chỉ nn":            "chứng chỉ ngoại ngữ",
    # CNTT
    "it":                      "công nghệ thông tin",
    "cntt":                    "công nghệ thông tin",
    "tin học":                 "công nghệ thông tin",
    # Chuyển đổi tín chỉ
    "chuyển trường":           "chuyển đổi tín chỉ",
    "cdtc":                    "chuyển đổi tín chỉ",
    "cong nhan ket qua":       "công nhận kết quả học tập",
}

def normalize_query(query: str) -> str:
    """
    Chuẩn hóa query trước khi retrieval:
    - Viết thường
    - Thay thế viết tắt / từ đồng nghĩa
    Không xóa ký tự tiếng Việt.
    """
    q = query.lower().strip()
    # Áp dụng từ dài trước để tránh match sai từ ngắn hơn
    for alias, canonical in sorted(_SYNONYMS.items(), key=lambda x: -len(x[0])):
        if alias in q:
            q = q.replace(alias, canonical)
    return q


# ============================================================
# DETECT DOMAIN — nhận diện nhóm câu hỏi
# ============================================================

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "hoc_bong":     ["học bổng", "khuyến khích học tập", "kkht", "hbkkht"],
    "hoc_phi":      ["học phí", "nộp học phí", "miễn giảm học phí", "hoàn học phí"],
    "hoc_phan":     ["đăng ký học phần", "hủy học phần", "học phần", "tín chỉ", "đăng ký môn"],
    "ren_luyen":    ["rèn luyện", "điểm rèn luyện", "phân loại rèn luyện"],
    "canh_bao":     ["cảnh báo", "buộc thôi học", "kết quả học tập", "cảnh báo học tập"],
    "ngoai_ngu":    ["ngoại ngữ", "tiếng anh", "ielts", "toefl", "toeic", "chuẩn đầu ra ngoại ngữ"],
    "cntt":         ["công nghệ thông tin", "cntt", "tin học", "chứng chỉ cntt"],
    "chuyen_doi_tc":["chuyển đổi tín chỉ", "công nhận kết quả", "chuyển trường", "chứng chỉ nghề nghiệp"],
    "tot_nghiep":   ["tốt nghiệp", "xét tốt nghiệp", "điều kiện tốt nghiệp", "bằng tốt nghiệp"],
    "lich_hoc":     ["lịch học", "thời khóa biểu", "ca học", "buổi học", "tiến độ"],
}

# Domain → từ khóa xuất hiện trong ten_van_ban / so_hieu của chunk
_DOMAIN_DOC_HINTS: dict[str, list[str]] = {
    "hoc_bong":      ["khuyến khích", "kkht", "học bổng"],
    "hoc_phi":       ["học phí", "miễn giảm"],
    "hoc_phan":      ["2833", "đăng ký", "hủy học phần"],
    "ren_luyen":     ["rèn luyện"],
    "canh_bao":      ["335", "cảnh báo", "thôi học"],
    "ngoai_ngu":     ["3337", "ngoại ngữ", "chuẩn đầu ra"],
    "cntt":          ["3337", "công nghệ thông tin"],
    "chuyen_doi_tc": ["2786", "309", "chuyển đổi", "công nhận"],
    "tot_nghiep":    ["335", "tốt nghiệp"],
    "lich_hoc":      ["lịch", "ca học", "tiến độ"],
}

def detect_domain(query: str) -> str | None:
    """
    Nhận diện domain của câu hỏi.
    Trả về tên domain (str) hoặc None nếu không xác định.
    Domain dùng để filter_docs và debug log.
    """
    q = query.lower()
    # Đếm keyword match cho mỗi domain, lấy domain có nhiều nhất
    best_domain = None
    best_count  = 0
    for domain, kws in _DOMAIN_KEYWORDS.items():
        count = sum(1 for kw in kws if kw in q)
        if count > best_count:
            best_count  = count
            best_domain = domain
    return best_domain if best_count > 0 else None


# ============================================================
# FILTER DOCS BY DOMAIN — lọc ứng viên trước khi đưa sang LLM
# ============================================================

def filter_docs_by_domain(
    docs:   list[Document],
    domain: str | None,
    top_k:  int = 5,
) -> list[Document]:
    """
    Nếu detect được domain → ưu tiên đẩy chunk thuộc đúng domain lên đầu.
    Không xóa chunk nào — chỉ sắp xếp lại để LLM đọc phần liên quan trước.
    Nếu không đủ chunk domain → bổ sung chunk không thuộc domain vào cuối.
    """
    if domain is None or domain not in _DOMAIN_DOC_HINTS:
        return docs[:top_k]

    hints = [h.lower() for h in _DOMAIN_DOC_HINTS[domain]]

    def _match_domain(doc: Document) -> bool:
        ten  = doc.metadata.get("ten_van_ban",  "").lower()
        sid  = doc.metadata.get("so_hieu",      "").lower()
        sec  = doc.metadata.get("section_title","").lower()
        kws  = doc.metadata.get("keywords",     "").lower()  # co o ca NQ lan thuong
        hay  = ten + " " + sid + " " + sec + " " + kws
        return any(h in hay for h in hints)

    in_domain  = [d for d in docs if _match_domain(d)]
    out_domain = [d for d in docs if not _match_domain(d)]

    merged = in_domain + out_domain
    return merged[:top_k]


# ============================================================
# SIMPLE RERANK — boost chunk có nhiều từ query xuất hiện
# ============================================================

def simple_rerank(
    docs:  list[Document],
    query: str,
    top_k: int = 5,
) -> list[Document]:
    """
    Rerank đơn giản bằng cách đếm số từ query có trong page_content.
    Dùng sau filter_docs để sắp xếp lại thứ tự trước khi đưa vào LLM.
    Không xóa doc — chỉ thay đổi thứ tự.

    Tại sao cần:
        Vector search xếp hạng theo cosine similarity toàn đoạn.
        Rerank keyword bổ sung tín hiệu "chunk này có đúng từ user hỏi không?"
        → giúp chunk có "học bổng KKHT điều 5" lên trước chunk chỉ nói chung chung.
    """
    q_words = set(w for w in re.split(r"\s+", query.lower()) if len(w) >= 3)
    if not q_words:
        return docs[:top_k]

    def _score(doc: Document) -> float:
        text = doc.page_content.lower()
        return sum(1 for w in q_words if w in text) / len(q_words)

    scored = sorted(docs, key=_score, reverse=True)
    return scored[:top_k]

def _tokenize_vi(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(
        r"[^\w\sàáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]",
        " ", text
    )
    tokens  = [t for t in text.split() if len(t) > 1]
    bigrams = [f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)]
    return tokens + bigrams


class BM25:
    """
    BM25 với numpy+scipy sparse matrix.
    Kết quả HOÀN TOÀN GIỐNG pure-Python — chỉ nhanh hơn ~5-10×.
    """

    def __init__(self, docs: list[Document], k1: float = 1.5, b: float = 0.75):
        self.docs   = docs
        self.k1     = k1
        self.b      = b
        self.n      = len(docs)
        self.corpus = [_tokenize_vi(d.page_content) for d in docs]

        all_terms  = set(t for tok in self.corpus for t in tok)
        self.vocab = {t: i for i, t in enumerate(all_terms)}
        V          = len(self.vocab)

        dl         = [len(tok) for tok in self.corpus]
        self.avgdl = sum(dl) / max(self.n, 1)

        df = [0] * V
        for tok in self.corpus:
            for t in set(tok):
                if t in self.vocab:
                    df[self.vocab[t]] += 1

        # IDF giống hệt bản gốc — Robertson-Sparck Jones
        self.idf = [
            math.log((self.n - max(df[i], 1) + 0.5) / (max(df[i], 1) + 0.5) + 1)
            for i in range(V)
        ]

        self._numpy_ready = False
        if _NUMPY_OK:
            self._init_numpy(dl, V)

    def _init_numpy(self, dl: list, V: int):
        self._idf_np = np.array(self.idf, dtype=np.float32)
        dl_np        = np.array(dl, dtype=np.float32)
        # norm vector: k1*(1 - b + b*dl/avgdl) — giống hệt công thức gốc
        self._norm   = self.k1 * (1 - self.b + self.b * dl_np / self.avgdl)

        rows, cols, vals = [], [], []
        for di, tok in enumerate(self.corpus):
            cnt: dict[int, int] = {}
            for t in tok:
                if t in self.vocab:
                    vi = self.vocab[t]
                    cnt[vi] = cnt.get(vi, 0) + 1
            for vi, tf in cnt.items():
                rows.append(di); cols.append(vi); vals.append(float(tf))

        if _SCIPY_OK:
            self._tf     = csr_matrix((vals, (rows, cols)), shape=(self.n, V), dtype=np.float32)
            self._sparse = True
        else:
            self._tf     = np.zeros((self.n, V), dtype=np.float32)
            for r, c, v in zip(rows, cols, vals):
                self._tf[r, c] = v
            self._sparse = False

        self._numpy_ready = True

    def _score_numpy(self, q_ids: list[int]) -> "np.ndarray":
        scores = np.zeros(self.n, dtype=np.float32)
        for vi in q_ids:
            idf = self._idf_np[vi]
            if idf <= 0:
                continue
            tf_vec = (
                np.asarray(self._tf[:, vi].todense()).flatten()
                if self._sparse else self._tf[:, vi]
            )
            # Công thức BM25 giống hệt bản gốc, chỉ vectorized
            num    = tf_vec * (self.k1 + 1)
            denom  = tf_vec + self._norm
            scores += idf * (num / np.maximum(denom, 1e-9))
        return scores

    def retrieve(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        q_tokens = _tokenize_vi(query)
        q_ids    = list({self.vocab[t] for t in q_tokens if t in self.vocab})
        if not q_ids:
            return []

        if self._numpy_ready:
            arr     = self._score_numpy(q_ids)
            top_idx = arr.argsort()[::-1][:k]
            return [(self.docs[i], float(arr[i])) for i in top_idx if arr[i] > 0]

        # Pure-Python fallback — giống bản gốc
        from collections import Counter
        results = []
        for di, tok in enumerate(self.corpus):
            tf_map = Counter(tok)
            dl     = len(tok)
            score  = 0.0
            for t in set(q_tokens):
                if t not in self.vocab:
                    continue
                vi  = self.vocab[t]
                tf  = tf_map.get(t, 0)
                if tf == 0:
                    continue
                num   = tf * (self.k1 + 1)
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += self.idf[vi] * (num / max(denom, 1e-9))
            if score > 0:
                results.append((di, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return [(self.docs[i], s) for i, s in results[:k]]


# ============================================================
# CACHE RETRIEVAL — chỉ cache, không thay đổi kết quả
# ============================================================

class LRUQueryCache:
    def __init__(self, maxsize: int = RETRIEVAL_CACHE_SIZE):
        self._cache   = OrderedDict()
        self._maxsize = maxsize
        self._lock    = asyncio.Lock()

    @staticmethod
    def _key(q: str) -> str:
        q = re.sub(r"\s+", " ", q.lower().strip()).rstrip("?!.,;:")
        return hashlib.sha256(q.encode()).hexdigest()[:16]

    async def get(self, q: str):
        key = self._key(q)
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    async def set(self, q: str, docs):
        key = self._key(q)
        async with self._lock:
            self._cache[key] = docs
            self._cache.move_to_end(key)
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)


_cache = LRUQueryCache()


# ============================================================
# HYBRID RETRIEVER — BM25 + Vector chạy SONG SONG
# Weights và k giữ nguyên bản gốc (vector=0.6, bm25=0.4, k=5)
# ============================================================

def _rrf_score(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank)


class HybridRetriever:
    """
    Giống bản gốc về logic, khác ở:
    1. BM25 dùng numpy (nhanh hơn, kết quả như cũ)
    2. Vector + BM25 chạy song song qua asyncio.gather()
    3. Có cache cho câu hỏi lặp lại
    Weights, k, RRF formula: giữ nguyên 100%.
    """

    def __init__(self,
                 vs,
                 bm25_docs: list[Document],
                 k: int             = 5,
                 vector_weight: float = 0.6,
                 bm25_weight:   float = 0.4):

        self._vs            = vs
        self._bm25          = BM25(bm25_docs)
        self._k             = k
        self._vw            = vector_weight
        self._bw            = bm25_weight
        self._vec_retriever = get_smart_retriever(vs, k=k * 3)

        mode = "numpy+scipy" if _SCIPY_OK else ("numpy" if _NUMPY_OK else "pure-Python")
        print(f"    BM25 index: {len(bm25_docs)} docs [{mode}]")
        print(f"    Vector store: ready")
        print(f"    Weights: vector={vector_weight}, BM25={bm25_weight}")

    def _merge(self,
               vec_results:  list[Document],
               bm25_results: list[tuple[Document, float]]) -> list[Document]:
        """RRF merge — giống hệt bản gốc."""
        rrf_scores: dict[str, float]    = {}
        doc_map:    dict[str, Document] = {}

        for rank, doc in enumerate(vec_results):
            key = doc.metadata.get("chunk_id", doc.page_content[:80])
            rrf_scores[key]  = rrf_scores.get(key, 0) + self._vw * _rrf_score(rank)
            doc_map[key]     = doc

        for rank, (doc, _) in enumerate(bm25_results):
            key = doc.metadata.get("chunk_id", doc.page_content[:80])
            rrf_scores[key]  = rrf_scores.get(key, 0) + self._bw * _rrf_score(rank)
            if key not in doc_map:
                doc_map[key] = doc

        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return [doc_map[key] for key, _ in ranked[:self._k]]

    def invoke(self, query: str) -> list[Document]:
        return self._merge(
            self._vec_retriever.invoke(query),
            self._bm25.retrieve(query, k=self._k * 3)
        )

    async def ainvoke(self, query: str) -> list[Document]:
        # Cache hit
        cached = await _cache.get(query)
        if cached is not None:
            print("    [CACHE HIT]")
            return cached

        loop = asyncio.get_event_loop()

        # Chạy song song — kết quả giống tuần tự vì 2 task độc lập
        vec_res, bm25_res = await asyncio.gather(
            loop.run_in_executor(None, self._vec_retriever.invoke, query),
            loop.run_in_executor(None, self._bm25.retrieve, query, self._k * 3),
        )

        docs = self._merge(vec_res, bm25_res)
        await _cache.set(query, docs)
        return docs

    def invoke_with_scores(self, query: str) -> list[tuple[Document, float, str]]:
        """Debug — giống bản gốc."""
        vec_results  = self._vec_retriever.invoke(query)
        bm25_results = self._bm25.retrieve(query, k=self._k * 3)
        rrf_scores: dict[str, float]     = {}
        sources:    dict[str, list[str]] = {}
        doc_map:    dict[str, Document]  = {}
        for rank, doc in enumerate(vec_results):
            key = doc.metadata.get("chunk_id", doc.page_content[:80])
            s   = self._vw * _rrf_score(rank)
            rrf_scores[key] = rrf_scores.get(key, 0) + s
            sources.setdefault(key, []).append(f"vector(rank={rank+1}, +{s:.3f})")
            doc_map[key] = doc
        for rank, (doc, bm25_s) in enumerate(bm25_results):
            key = doc.metadata.get("chunk_id", doc.page_content[:80])
            s   = self._bw * _rrf_score(rank)
            rrf_scores[key] = rrf_scores.get(key, 0) + s
            sources.setdefault(key, []).append(f"bm25(rank={rank+1}, score={bm25_s:.2f}, +{s:.3f})")
            if key not in doc_map:
                doc_map[key] = doc
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return [(doc_map[k], sc, " | ".join(sources.get(k, [])))
                for k, sc in ranked[:self._k]]

    def get_relevant_documents(self, query: str) -> list[Document]:
        return self.invoke(query)


# ============================================================
# KHỞI TẠO
# ============================================================

def _load_all_docs_for_bm25(vs) -> list[Document]:
    try:
        result = vs._collection.get(include=["documents", "metadatas"])
        docs = [
            Document(page_content=text, metadata=meta or {})
            for text, meta in zip(result["documents"], result["metadatas"])
        ]
        print(f"    Loaded {len(docs)} docs từ ChromaDB cho BM25 index")
        return docs
    except Exception as e:
        print(f"    Không load được docs cho BM25: {e}")
        return []


print(">>> KHỞI TẠO HYBRID RETRIEVER...")
_all_docs = _load_all_docs_for_bm25(vector_store)
retriever = HybridRetriever(
    vs=vector_store,
    bm25_docs=_all_docs,
    k=5,
    vector_weight=0.6,
    bm25_weight=0.4,
)

print(f">>> ĐANG GỌI NÃO AI ({LLM_MODEL})...")
# streaming=True để token chạy ra ngay — không ảnh hưởng nội dung
model = OllamaLLM(model=LLM_MODEL, streaming=True)

# 1 prompt duy nhất — model tự quyết định cách trích dẫn dựa vào nội dung thực tế:
# - Chunk có "Điều X" → tự trích dẫn "Theo Điều X của QĐ..."
# - Chunk không có Điều (thông báo, tiến độ, lịch học) → trả lời tự nhiên
# Không dùng 2 prompt riêng → tránh sai khi câu hỏi có cả 2 loại tài liệu
TEMPLATE = """Bạn là trợ lý tư vấn của Học viện Ngân hàng (HVNH). Dưới đây là tài liệu liên quan:

[TÀI LIỆU]
{reviews}

Dựa HOÀN TOÀN vào tài liệu trên, trả lời câu hỏi sau bằng tiếng Việt.

Hướng dẫn trích dẫn:
- Nếu thông tin đến từ một Điều trong quyết định/quy chế → ghi rõ: "Theo Điều [số] của [số hiệu]..."
- Nếu thông tin đến từ thông báo, lịch học, tiến độ chương trình → trả lời bình thường, ghi tên tài liệu nguồn nếu cần.
- Không bịa đặt, không thêm thông tin ngoài tài liệu.
- Nếu tài liệu không có thông tin cần thiết → trả lời: "Xin lỗi, tôi không tìm thấy nội dung liên quan đến câu hỏi của bạn trong các tài liệu được cung cấp. Hãy hỏi lại vấn đề bạn cần tư vấn về quy chế, quy định của Học viện Ngân hàng nhé."

[CÂU HỎI]
{question}

[TRẢ LỜI]"""

prompt = ChatPromptTemplate.from_template(TEMPLATE)
chain  = prompt | model


def format_docs(docs: list[Document]) -> str:
    """Giữ NGUYÊN bản gốc — không cắt nội dung, không giới hạn doc."""
    parts = []
    for doc in docs:
        meta    = doc.metadata
        dieu    = meta.get("dieu_so", "")
        title   = meta.get("dieu_title", "")
        so_hieu = meta.get("so_hieu", "")
        content = meta.get("full_text") or doc.page_content
        header  = f"[Điều {dieu} - {title} | {so_hieu}]" if dieu else f"[{so_hieu}]"
        parts.append(f"{header}\n{content}")
    sep = "\n\n" + "─" * 50 + "\n\n"
    return sep.join(parts)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="HVBot RAG API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    userId:  str  = None
    stream:  bool = False


@app.post("/api/chat")
async def chat_api(request: ChatRequest):
    try:
        question = request.message
        print(f"\n[USER] {question}")

        # 1. Normalize query — thay viết tắt / đồng nghĩa
        q_norm   = normalize_query(question)
        domain   = detect_domain(q_norm)
        print(f"[NORM] {q_norm}")
        print(f"[DOMAIN] {domain or '(khong xac dinh)'}")

        # 2. Retrieval async — song song, có cache
        raw_docs = await retriever.ainvoke(q_norm)

        # 3. Filter theo domain — ưu tiên chunk đúng nhóm lên đầu
        filtered = filter_docs_by_domain(raw_docs, domain, top_k=MAX_CONTEXT_DOCS)

        # 4. Rerank — boost chunk có nhiều từ query
        final_docs = simple_rerank(filtered, q_norm, top_k=MAX_CONTEXT_DOCS)

        # 5. Debug log — in top kết quả trước khi gọi model
        print(f"[RETRIEVE] {len(raw_docs)} raw -> {len(filtered)} filtered -> {len(final_docs)} final")
        for i, doc in enumerate(final_docs):
            meta    = doc.metadata
            cid     = meta.get("chunk_id", "?")
            so_hieu = meta.get("so_hieu",  meta.get("ten_van_ban", "?"))[:30]
            dieu    = meta.get("dieu_so",  meta.get("section_title", ""))
            print(f"  [{i+1}] {cid} | {so_hieu} | dieu/section={dieu}")

        reviews_text = format_docs(final_docs)

        # 6. Streaming
        if request.stream:
            async def token_generator():
                print("AI dang stream...")
                try:
                    async for chunk in chain.astream(
                        {"reviews": reviews_text, "question": question}
                    ):
                        yield chunk
                except Exception as e:
                    yield f"\n[Lỗi hệ thống: {e}]"

            return StreamingResponse(
                token_generator(),
                media_type="text/plain; charset=utf-8",
            )

        # 7. Non-streaming
        print("AI dang suy nghi...")
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: chain.invoke({"reviews": reviews_text, "question": question})
        )
        print("[XONG]")
        return {"reply": result}

    except Exception as e:
        print(f"[LOI]: {e}")
        return {"reply": f"Hệ thống gặp lỗi kỹ thuật: {str(e)}"}


class DebugRetrieveRequest(BaseModel):
    query: str
    k:     int = 3

@app.post("/api/debug_retrieve")
async def debug_retrieve(request: DebugRetrieveRequest):
    """Endpoint phục vụ evaluate.py để đo Recall@K."""
    q_norm = normalize_query(request.query)
    docs   = await retriever.ainvoke(q_norm)
    domain = detect_domain(q_norm)
    filtered = filter_docs_by_domain(docs, domain, top_k=request.k)
    final    = simple_rerank(filtered, q_norm, top_k=request.k)
    return {
        "chunk_ids": [d.metadata.get("chunk_id", "") for d in final[:request.k]],
        "so_hieus":  [d.metadata.get("so_hieu",   "") for d in final[:request.k]],
        "query_normalized": q_norm,
        "domain":    domain,
    }


@app.get("/api/health")
async def health():
    mode = "numpy+scipy" if _SCIPY_OK else ("numpy" if _NUMPY_OK else "pure-Python")
    return {
        "status":       "ok",
        "model":        LLM_MODEL,
        "bm25_backend": mode,
        "cached":       len(_cache._cache),
        "max_cache":    RETRIEVAL_CACHE_SIZE,
    }


if __name__ == "__main__":
    print("\n" + "!" * 50)
    print(f"  MODEL:  {LLM_MODEL} (prompt tối ưu cho 3B)")
    print(f"  TỐI ƯU: BM25 numpy · parallel retrieval · cache · streaming")
    print(f"  API:    http://localhost:5000/api/chat")
    print(f"  HEALTH: http://localhost:5000/api/health")
    print(f"  DEBUG:  http://localhost:5000/api/debug_retrieve")
    print("!" * 50)
    uvicorn.run(app, host="0.0.0.0", port=5000)