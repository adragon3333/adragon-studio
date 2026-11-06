# 🚀 Hướng Dẫn Phát Triển & Đẩy Code (Git Workflow) — Adragon Studio

Tài liệu hướng dẫn chi tiết dành cho bạn khi về nhà hoặc làm việc trên máy khác để phát triển tính năng mới và đồng bộ lên GitHub.

---

## 📥 1. Lần đầu tải dự án về máy mới

Nếu bạn đổi sang máy tính khác, hãy mở **Terminal (PowerShell / Command Prompt)** và làm các bước sau:

```powershell
# 1. Clone repository về máy
git clone https://github.com/adragon3333/adragon-studio.git

# 2. Di chuyển vào thư mục dự án
cd adragon-studio

# 3. Tạo môi trường ảo và cài đặt thư viện (khuyến nghị dùng uv để cài cực nhanh)
# Nếu chưa có uv, cài bằng: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv venv
uv pip install -e .
uv pip install fastapi uvicorn soundfile librosa pymupdf
```

> **Cách chạy nhanh**: Nhấp đúp chuột vào file `run_adragon_studio.bat` trong thư mục là hệ thống tự khởi động tại `http://localhost:8000`.

---

## 🔄 2. Quy trình 3 bước chuẩn khi cập nhật tính năng mới

Mỗi khi bạn chỉnh sửa giao diện (`client/unified.html`), logic server (`apps/unified_server.py`), hoặc thêm dịch vụ mới, hãy làm theo quy trình sau:

### Bước 1: Kiểm tra xem mình đã sửa những file nào
```bash
git status
```
*(Git sẽ liệt kê màu đỏ các file bạn vừa thay đổi).*

### Bước 2: Gom các file đã sửa vào chuẩn bị lưu
```bash
# Gom toàn bộ tất cả các file vừa sửa:
git add .

# Hoặc nếu chỉ muốn gom riêng 1 file cụ thể:
git add client/unified.html
```

### Bước 3: Tạo Commit (Ghi chú nội dung vừa làm)
```bash
git commit -m "Thêm tính năng A / Sửa giao diện B"
```
*(Hãy viết mô tả ngắn gọn, dễ hiểu về nội dung bạn vừa cập nhật).*

### Bước 4: Đẩy (Push) lên GitHub
```bash
git push
```
*(Xong! Code mới sẽ xuất hiện ngay lập tức trên GitHub của bạn).*

---

## 🛠️ 3. Các câu lệnh Git hữu ích thường dùng

| Tình huống | Lệnh thực hiện | Giải thích |
| :--- | :--- | :--- |
| **Kéo code mới nhất về** | `git pull origin main` | Lấy các thay đổi mới nhất từ GitHub về máy hiện tại (dùng khi bạn sửa code ở máy khác). |
| **Xem lịch sử các commit gần đây** | `git log --oneline -5` | Xem 5 lần commit gần nhất kèm mã commit và nội dung ghi chú. |
| **Lỡ sửa sai muốn quay lại bản cũ** | `git restore <tên_file>` | Khôi phục file về trạng thái commit trước đó, bỏ các sửa đổi chưa commit. |
| **Bỏ gom file khỏi git add** | `git restore --staged <tên_file>` | Bỏ file ra khỏi hàng đợi commit nếu lỡ gõ `git add`. |

---

## 📂 4. Cấu trúc các file quan trọng cần lưu ý

* `client/unified.html`: Toàn bộ giao diện Web, Sidebar, CSS, Icons Lucide và JavaScript gọi API.
* `apps/unified_server.py`: Backend FastAPI trung tâm điều khiển TTS, Voice Cloning, dọn dẹp lịch sử 24h và các dịch vụ.
* `outputs/references/`: Thư mục lưu file audio mẫu của các giọng Clone (được lưu vĩnh viễn).
* `outputs/generated/custom_voices.json`: Danh sách hồ sơ các giọng bạn đã nhân bản.
* `run_adragon_studio.bat`: File nhấp đúp chạy nhanh trên Windows.

---
🔗 **Repository GitHub:** [https://github.com/adragon3333/adragon-studio](https://github.com/adragon3333/adragon-studio)
