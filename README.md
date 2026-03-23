# 🎓 HVBot — Chatbot tư vấn học tập cho sinh viên Học viện Ngân hàng

![Build Status](https://img.shields.io/badge/build-passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)
![Version](https://img.shields.io/badge/version-1.0.0-orange)
![LangChain](https://img.shields.io/badge/LangChain-0.3-purple)
![Ollama](https://img.shields.io/badge/Ollama-local-black)

> 🤖 Hệ thống chatbot RAG (Retrieval-Augmented Generation) hoạt động hoàn toàn offline, giúp sinh viên Học viện Ngân hàng tra cứu quy chế, quy định nội bộ nhanh chóng và chính xác bằng ngôn ngữ tự nhiên.

---

## 📋 Mục lục

- [Giới thiệu dự án](#-giới-thiệu-dự-án)
- [Tính năng chính](#-tính-năng-chính)
- [Kiến trúc hệ thống](#-kiến-trúc-hệ-thống)
- [Tech stack](#-tech-stack)
- [Thách thức & hướng phát triển](#-thách-thức--hướng-phát-triển)
- [Cài đặt & cấu hình](#-cài-đặt--cấu-hình)
- [Hướng dẫn sử dụng](#-hướng-dẫn-sử-dụng)
- [Đánh giá chất lượng](#-đánh-giá-chất-lượng)
- [Đóng góp](#-đóng-góp)
- [Credits](#-credits)
- [Giấy phép](#-giấy-phép)

---

## 📖 Giới thiệu dự án

**HVBot** là hệ thống hỏi-đáp thông minh được xây dựng theo kiến trúc RAG, cho phép sinh viên Học viện Ngân hàng (HVNH) đặt câu hỏi bằng ngôn ngữ tự nhiên và nhận câu trả lời được **trích dẫn điều khoản cụ thể** từ văn bản gốc — thay vì phải đọc thủ công hàng chục file Word dài hàng trăm trang.

Hệ thống chạy **hoàn toàn offline** nhờ mô hình ngôn ngữ cục bộ qua Ollama, không gửi bất kỳ dữ liệu nào ra ngoài, phù hợp với môi trường nội bộ yêu cầu bảo mật thông tin.

---

## ✨ Tính năng chính

- 🔍 **Hybrid search (BM25 + Vector + RRF)** — kết hợp tìm kiếm từ khóa chính xác và tìm kiếm ngữ nghĩa, hợp nhất kết quả bằng Reciprocal Rank Fusion
- ⚡ **BM25 vectorized với numpy/scipy** — nhanh hơn 5–10× so với pure-Python, kết quả hoàn toàn tương đương; tự động fallback về pure-Python nếu thiếu thư viện
- 🔄 **Retrieval song song (asyncio)** — vector search và BM25 chạy đồng thời qua `asyncio.gather()`, giảm latency đáng kể
- 💾 **LRU cache cho truy vấn lặp lại** — cache 50 câu hỏi gần nhất bằng SHA-256 key, tránh re-compute không cần thiết
- 🌊 **Streaming response** — token trả về từng phần ngay khi LLM generate, giao diện hiển thị mượt mà
- 📄 **Chunking thông minh theo 3 loại văn bản:**
  - *Văn bản pháp quy* (quyết định, nghị quyết): phân tách theo cấu trúc Điều → Khoản (hierarchical parent-child)
  - *Văn bản hành chính* (thông báo có mục tiêu, kế hoạch): giữ nguyên từng section hoàn chỉnh, không cắt nhỏ
  - *Văn bản thông tin* (lịch học, tiến độ đào tạo): tách theo header; file tiến độ giữ nguyên 1 chunk để retrieve đủ tất cả học kỳ
- 🧠 **Parent-child retrieval** — tìm khoản con, tự động fetch điều cha để LLM có đủ ngữ cảnh
- 🏷️ **Keyword metadata tự động** — mỗi chunk được gắn thẻ từ khóa (học bổng, IELTS, TOEIC, cảnh báo học vụ...) để hỗ trợ rerank chính xác hơn
- 📝 **Trích dẫn nguồn thông minh** — tự nhận biết loại tài liệu để trích dẫn phù hợp (số điều với pháp quy; tên tài liệu với thông báo, lịch học)
- 🌐 **Giao diện web chat** — trò chuyện qua trình duyệt, không cần cài thêm phần mềm
- 🩺 **Health endpoint** — `/api/health` kiểm tra trạng thái model, BM25 backend và cache
- 📊 **Bộ đánh giá RAG tích hợp** — đo 5 chỉ số tự động: TTFT, TPS, Answer Accuracy, Recall@K, Faithfulness; xuất kết quả ra file Excel

---

## 🏗️ Kiến trúc hệ thống

```
File .docx (data/processed/)
         │
         ▼
┌──────────────────────────────────┐
│       Phân loại & chunking       │
│  ┌───────────────────────────┐   │
│  │ Pháp quy (QĐ/NQ/TT)      │──►│ chunking_NQ.py      → Điều / Khoản
│  │ Hành chính (TB/CV/HD/KH)  │──►│ chunking_thuong.py  → Section giữ nguyên
│  │ Thông tin / tiến độ       │──►│ chunking_thuong.py  → Paragraph / 1 chunk
│  └───────────────────────────┘   │
└──────────────┬───────────────────┘
               │  Chunks + metadata (so_hieu, keywords, kieu_van_ban...)
               ▼
┌──────────────────────────────────┐
│  Embedding: nomic-embed-text     │
│  (via Ollama, chạy local)        │
└──────────────┬───────────────────┘
               ▼
┌──────────────────────────────────┐
│  ChromaDB (vector store local)   │
└──────────────┬───────────────────┘
               ▼
┌──────────────────────────────────────────────┐
│              HybridRetriever                 │
│  ┌──────────────────┐  ┌──────────────────┐  │
│  │  BM25 (40%)      │  │  Vector (60%)    │  │
│  │  numpy+scipy     │  │  ChromaDB sim.   │  │
│  └────────┬─────────┘  └────────┬─────────┘  │
│           └──── asyncio.gather() ────┘         │
│              RRF Fusion + parent fetch         │
│           LRU cache (50 queries)               │
└─────────────────┬────────────────────────────┘
                  │  Top-5 chunks có ngữ cảnh đầy đủ
                  ▼
      ┌───────────────────────────┐
      │  Ollama LLM (qwen2.5:3b)  │
      │  streaming=True           │
      └─────────────┬─────────────┘
                    │  StreamingResponse / JSON
                    ▼
      ┌─────────────────────────────────┐
      │  FastAPI (port 5000)            │◄──► Web frontend (HTML/CSS/JS)
      │  /api/chat  /api/health         │
      └─────────────────────────────────┘
                    ▲
                    │  Gọi API để đo chỉ số
      ┌─────────────────────────────────┐
      │  evaluate.py                    │
      │  TTFT · TPS · Accuracy          │
      │  Recall@K · Faithfulness        │──► Result_evaluate.xlsx
      └─────────────────────────────────┘
```

---

## 🛠️ Tech stack

| Thành phần | Công nghệ | Ghi chú |
|---|---|---|
| Mô hình ngôn ngữ | Ollama + `qwen2.5:3b` | Chạy local, không cần API key |
| Embedding | `nomic-embed-text` (via Ollama) | Đa ngôn ngữ, hỗ trợ tiếng Việt |
| Vector database | ChromaDB | Nhẹ, nhúng trực tiếp, không cần server |
| Framework AI | LangChain 0.3 | Orchestration pipeline RAG |
| Backend API | FastAPI + Uvicorn | Async, streaming, tự sinh OpenAPI docs |
| BM25 backend | NumPy + SciPy sparse matrix | Tự động fallback pure-Python nếu thiếu |
| Giao diện | HTML / CSS / JavaScript | Chat qua trình duyệt, không cần framework |
| Đọc file Word | `docx2txt` | Parse `.docx` thuần Python |
| Đánh giá RAG | `httpx` + `openpyxl` | Async HTTP client + xuất báo cáo Excel |
| Ngôn ngữ lập trình | Python 3.10+ | — |

---

## ⚡ Thách thức & hướng phát triển

### Thách thức đã gặp

- **Cấu trúc văn bản pháp quy phức tạp** — header/footer lặp lại, pandoc xuất markdown không nhất quán. Giải pháp: pipeline tiền xử lý regex chuyên biệt trong `chunking_NQ.py`
- **Phân biệt loại văn bản thường** — thông báo hành chính và văn bản thông tin cần chiến lược chunking khác nhau. Giải pháp: `_detect_kieu()` tự động nhận diện, file tiến độ giữ nguyên 1 chunk toàn bộ
- **Giới hạn context window embedding** — điều khoản dài vượt token limit. Giải pháp: `_safe_embed_text()` tách sub-chunk thông minh có overlap tại ranh giới
- **Tokenization tiếng Việt** — tokenizer mặc định không hiểu từ ghép ("tín chỉ", "học phần"). Giải pháp: BM25 tự xây với bigram tokenizer
- **Hiệu năng BM25 trên corpus lớn** — pure-Python chậm khi số chunk tăng. Giải pháp: vectorized BM25 với numpy sparse matrix, nhanh hơn 5–10×, kết quả không đổi
- **Latency retrieval** — BM25 và vector search chạy tuần tự. Giải pháp: `asyncio.gather()` song song + LRU cache

### Hướng phát triển tương lai

- [ ] Tích hợp reranker (cross-encoder) để tăng độ chính xác sau bước retrieval
- [ ] Hỗ trợ hội thoại đa lượt (multi-turn conversation với memory)
- [ ] Giao diện admin để upload và quản lý văn bản mới không cần CLI
- [ ] Mở rộng bộ test đánh giá lên 50–100 câu hỏi, bổ sung chỉ số MRR và NDCG
- [ ] Đóng gói Docker để triển khai lên server
- [ ] Cập nhật tài liệu realtime từ website HVNH qua crawler tự động theo lịch

---

## 🚀 Cài đặt & cấu hình

### Yêu cầu hệ thống

- Python 3.10+
- [Ollama](https://ollama.com/) đã cài đặt và đang chạy
- RAM tối thiểu 8 GB (khuyến nghị 16 GB)

### 1. Clone repository

```bash
git clone https://github.com/[TODO]/[TODO].git
cd [TODO]
```

### 2. Cài đặt dependencies

```bash
pip install -r requirements.txt

# Tuỳ chọn nhưng khuyến nghị: numpy + scipy để BM25 nhanh hơn 5–10×
pip install numpy scipy

# Cần thêm cho evaluate.py
pip install httpx openpyxl
```

### 3. Tải model Ollama về máy

```bash
# Mô hình ngôn ngữ (LLM)
ollama pull qwen2.5:3b

# Mô hình embedding
ollama pull nomic-embed-text
```

### 4. Chuẩn bị dữ liệu

Đặt tất cả file văn bản `.docx` vào thư mục `data/processed/`:

```bash
mkdir -p data/processed

# Sao chép file Word vào thư mục
cp /path/to/your/documents/*.docx data/processed/
```

Cấu trúc thư mục:

```
data/
├── processed/     ← Đặt file .docx vào đây (bắt buộc)
├── raw/           ← File nguồn chưa xử lý (tuỳ chọn)
└── pdf/           ← File PDF gốc (tuỳ chọn)
```

### 5. Xây dựng vector database

```bash
python vector.py
```

Chương trình hiển thị menu tương tác — chọn **`[2]`** để nạp lần đầu:

```
=================================================================
  VECTOR DATABASE MANAGER
=================================================================
  [1] Dùng database hiện tại (không thay đổi)
  [2] Xóa và nạp lại toàn bộ từ đầu
  [3] Thêm file mới vào database hiện tại
  [0] Thoát
```

> ⏱️ Quá trình embedding có thể mất **5–15 phút** tuỳ số lượng văn bản và cấu hình máy.

---

## 📌 Hướng dẫn sử dụng

### Khởi động server

```bash
python main.py
```

Kết quả mong đợi:

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  MODEL:  qwen2.5:3b
  TỐI ƯU: BM25 numpy · parallel retrieval · cache · streaming
  API:    http://localhost:5000/api/chat
  HEALTH: http://localhost:5000/api/health
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

### Mở giao diện web

Mở file `HVBot_-_Trợ_lý_Học_viện_Ngân_Hàng.html` trực tiếp trong trình duyệt (Chrome / Firefox / Edge).

> ⚠️ **Quan trọng:** Phải khởi động `main.py` **trước**, sau đó mới mở file HTML.

### Giao diện chat

![Giao diện HVBot](./)

### Gọi API — chế độ thông thường

```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Điều kiện để được học vượt tín chỉ là gì?"}'
```

Kết quả mẫu:

```json
{
  "reply": "Theo Điều 5 của Quyết định 3337/QĐ-HVNH, sinh viên muốn học vượt cần đáp ứng các điều kiện: điểm trung bình tích lũy từ 2.5 trở lên và không có môn nào bị điểm F trong học kỳ liền trước..."
}
```

### Gọi API — chế độ streaming

```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Lịch học kỳ 2 năm nay như thế nào?", "stream": true}'
```

### Kiểm tra sức khỏe hệ thống

```bash
curl http://localhost:5000/api/health
```

```json
{
  "status": "ok",
  "model": "qwen2.5:3b",
  "bm25_backend": "numpy+scipy",
  "cached": 12,
  "max_cache": 50
}
```

### Cập nhật database khi có văn bản mới

```bash
python vector.py
# Chọn [3] để bổ sung file mới không xoá data cũ
# Hoặc chọn [2] để nạp lại hoàn toàn
```

### Kiểm tra chunking độc lập

```bash
# Văn bản pháp quy (quyết định, nghị quyết)
python chunking_NQ.py data/processed/ten_quyet_dinh.docx

# Văn bản thường (thông báo, công văn, tiến độ đào tạo)
python chunking_thuong.py data/processed/ten_thong_bao.docx
```

---

## 📊 Đánh giá chất lượng

Dự án tích hợp bộ đánh giá RAG tự động với **20 câu hỏi mẫu** trải rộng nhiều domain (ngoại ngữ, chuyển đổi tín chỉ, học bổng, kỷ luật...) và **5 chỉ số đo lường**:

| Chỉ số | Mô tả |
|---|---|
| **TTFT** (Time to First Token) | Thời gian từ lúc gửi câu hỏi đến khi nhận token đầu tiên |
| **TPS** (Tokens per Second) | Tốc độ sinh text của LLM |
| **Answer Accuracy** | Độ chính xác câu trả lời so với đáp án mẫu (keyword overlap) |
| **Recall@3** | Tỉ lệ retrieve đúng chunk trong top-3 kết quả |
| **Faithfulness** | Mức độ bám nguồn tài liệu, không bịa thêm thông tin (LLM-as-judge) |

### Chạy đánh giá

> ⚠️ Phải khởi động `main.py` trước khi chạy evaluate.

```bash
python evaluate.py
```

Kết quả được xuất ra:
- **Console** — bảng tóm tắt từng câu hỏi và chỉ số trung bình
- **`Result_evaluate.xlsx`** — báo cáo Excel có định dạng màu sắc, conditional formatting theo ngưỡng chất lượng

```
[1/20] Quy định 2786/QĐ-HVNH áp dụng cho đối tượng nào?
  TTFT=1.23s | TPS=18.4 tok/s | 142 tokens
  Accuracy: 0.87 (keyword match)
  Recall@3: HIT | ['2786/QĐ-HVNH__dieu_1']
  Faithfulness: 0.90/1.0 (raw=9/10)
...
════════════════════════ KẾT QUẢ TỔNG HỢP ════════════════════════
  TTFT trung bình    : 1.41s
  TPS trung bình     : 17.2 tok/s
  Accuracy trung bình: 0.82
  Recall@3           : 75.0%
  Faithfulness       : 0.88/1.0
```

---

## 🤝 Đóng góp

Mọi đóng góp đều được chào đón! Vui lòng làm theo các bước:

1. Fork repository này
2. Tạo branch mới: `git checkout -b feature/ten-tinh-nang`
3. Commit thay đổi: `git commit -m "feat: mô tả ngắn gọn"`
4. Push lên branch: `git push origin feature/ten-tinh-nang`
5. Tạo Pull Request và mô tả rõ những thay đổi đã thực hiện

### Quy ước commit message

```
feat:      Tính năng mới
fix:       Sửa lỗi
perf:      Cải thiện hiệu năng
docs:      Cập nhật tài liệu
refactor:  Cải thiện cấu trúc code, không thay đổi logic
test:      Thêm hoặc sửa test / bộ câu hỏi evaluate
chore:     Cấu hình, dependencies, việc vặt khác
```

### Mở rộng bộ câu hỏi đánh giá

Để thêm câu hỏi mới vào bộ test, chỉnh sửa danh sách `EVAL_DATASET` trong `evaluate.py`:

```python
{
    "query":            "Câu hỏi của bạn?",
    "expected_answer":  "đáp án mong đợi",
    "correct_chunk_ids": ["so_hieu__dieu_X"],
    "domain":           "ten_domain",
},
```

### Báo lỗi

Nếu phát hiện lỗi, vui lòng [mở issue](https://github.com/[TODO]/[TODO]/issues) với thông tin đầy đủ: mô tả lỗi, các bước tái hiện và log từ terminal.

---

## 🏆 Credits

### Nhóm phát triển

| Tên | Vai trò | GitHub |
|---|---|---|
| Đào Nguyên Chiến | Leader | [@Benhocchoi](https://github.com/Benhochoi) |
| Nguyễn Viết Việt Quốc | Developer | [@TODO](https://github.com/TODO) |
| Lê Thị Phượng | Developer | [@TODO](https://github.com/TODO) |
| Ngô Thuý Hạnh | Developer | [@TODO](https://github.com/TODO) |
| Lê Minh Tiểu Phượng | Developer | [@TODO](https://github.com/TODO) |

### Thư viện & tài liệu tham khảo

- [LangChain](https://python.langchain.com/) — Framework orchestration cho LLM pipeline
- [Ollama](https://ollama.com/) — Chạy mô hình ngôn ngữ cục bộ
- [ChromaDB](https://www.trychroma.com/) — Vector database nhẹ, nhúng trực tiếp vào ứng dụng
- [FastAPI](https://fastapi.tiangolo.com/) — Backend API async hiệu suất cao, hỗ trợ streaming
- [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) — Tính toán ma trận sparse cho BM25 vectorized
- [httpx](https://www.python-httpx.org/) — Async HTTP client dùng trong bộ đánh giá
- [openpyxl](https://openpyxl.readthedocs.io/) — Xuất báo cáo kết quả đánh giá ra Excel
- [Okapi BM25](https://en.wikipedia.org/wiki/Okapi_BM25) — Robertson & Sparck Jones, thuật toán xếp hạng tài liệu chuẩn
- [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — Cormack et al., SIGIR 2009

---

## 📄 Giấy phép

Dự án này được phân phối theo giấy phép **MIT**. Xem chi tiết tại file [`LICENSE`](./LICENSE).

---

<p align="center">Được xây dựng với ❤️ bởi nhóm Eternal Dreamee · Học viện Ngân hàng · 2026</p>