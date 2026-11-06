import os
import sys

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

from vieneu import Vieneu

print("=== Đang tải mô hình VieNeu-TTS (v3turbo - ONNX CPU) ===")
# Force ONNX backend for CPU execution without heavy PyTorch CUDA requirements
tts = Vieneu(mode="v3turbo", backend="onnx", precision="int8")

text = "Xin chào, đây là giọng đọc thử nghiệm từ hệ thống VieNeu TTS tiếng Việt chạy trực tiếp trên máy của bạn."
print(f"=== Đang tổng hợp giọng nói cho câu: '{text}' ===")

# Generate audio using default voice
audio = tts.infer(text=text, voice="Hải Đăng")

output_path = "output_test.wav"
tts.save(audio, output_path)
print(f"=== Đã tạo xong file âm thanh tại: {os.path.abspath(output_path)} ===")
