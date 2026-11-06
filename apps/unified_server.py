"""
Adragon Studio — Multi-Service AI Voice Platform.
Unified port, navigation menu, full TTS, Voice Cloning, Podcast, SRT, PDF.
Custom voices persistence and history management.
"""
import os
import sys
import io
import time
import wave
import uuid
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List
import asyncio
import urllib.parse
from datetime import datetime, timedelta

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import soundfile as sf
import librosa
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from vieneu import Vieneu
from apps.srt_speech import parse_srt, lay_on_timeline

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    fitz = None

SAMPLE_RATE = 48_000
ROOT_DIR = Path(__file__).resolve().parents[1]
CLIENT_HTML_PATH = ROOT_DIR / "client" / "unified.html"
OUTPUTS_DIR = ROOT_DIR / "outputs" / "generated"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
REFS_DIR = ROOT_DIR / "outputs" / "references"
REFS_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_INDEX = OUTPUTS_DIR / "history.json"
CUSTOM_VOICES_FILE = OUTPUTS_DIR / "custom_voices.json"

app = FastAPI(title="Adragon Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

tts_instance = None


def load_custom_voices(tts):
    if not CUSTOM_VOICES_FILE.exists():
        return
    try:
        voices = json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
        for v in voices:
            name = v["name"]
            ref_path = v["ref_path"]
            lang = v.get("language", "vi")
            if lang in ("ja", "japanese", "tiếng nhật"):
                continue  # Giọng tiếng Nhật do XTTS xử lý trực tiếp
            if os.path.exists(ref_path) and name not in tts._preset_voices:
                try:
                    tts.add_voice(
                        name=name,
                        ref_audio=ref_path,
                        description=v.get("description", "Giọng clone cá nhân"),
                        gender=v.get("gender", ""),
                        save=False
                    )
                    print(f"📢 Đã khôi phục giọng clone: {name}")
                except Exception as e:
                    print(f"⚠️ Lỗi khôi phục giọng {name}: {e}")
    except Exception as e:
        print(f"⚠️ Lỗi đọc custom_voices.json: {e}")


def get_tts():
    global tts_instance
    if tts_instance is None:
        precision = os.environ.get("VIENEU_PRECISION", "int8")
        print(f"⏳ Đang khởi động Adragon Studio Core ({precision}, ONNX CPU)...")
        tts_instance = Vieneu(
            mode="v3turbo",
            backend="onnx",
            precision=precision,
        )
        load_custom_voices(tts_instance)
        print("✅ Adragon Studio đã sẵn sàng!")
    return tts_instance


xtts_instance = None


def get_xtts():
    global xtts_instance
    if xtts_instance is None:
        os.environ["COQUI_TOS_AGREED"] = "1"
        os.environ["TTS_HOME"] = str(ROOT_DIR / "models_tts")
        import torch
        _orig_load = torch.load

        def _safe_load(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _orig_load(*args, **kwargs)

        torch.load = _safe_load

        from TTS.api import TTS
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        print(f"⏳ Đang nạp mô hình đa ngôn ngữ XTTS-v2 ({device})...")
        xtts_instance = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
        print("✅ XTTS-v2 đã sẵn sàng phục vụ Tiếng Nhật & Đa ngôn ngữ!")
    return xtts_instance


def cleanup_expired_history(max_age_hours: float = 24.0) -> int:
    """
    Tự động xóa các file audio sinh ra trong lịch sử nếu đã tồn tại quá 24 giờ.
    BẢO VỆ TUYỆT ĐỐI: Không bao giờ xóa kho giọng clone (outputs/references/ và custom_voices.json).
    """
    if not HISTORY_INDEX.exists():
        return 0

    try:
        items = json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return 0

    now = datetime.now()
    valid_items = []
    deleted_count = 0

    for it in items:
        created_at_str = it.get("created_at")
        is_expired = False
        if created_at_str:
            try:
                dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
                if (now - dt).total_seconds() > (max_age_hours * 3600):
                    is_expired = True
            except Exception:
                pass

        if is_expired:
            deleted_count += 1
            # Xóa file audio kết quả trong outputs/generated/
            fn = it.get("filename")
            if fn:
                fp = OUTPUTS_DIR / fn
                if fp.exists():
                    try:
                        os.remove(fp)
                    except Exception as e:
                        print(f"⚠️ Lỗi xóa file hết hạn {fn}: {e}")
        else:
            valid_items.append(it)

    if deleted_count > 0:
        HISTORY_INDEX.write_text(json.dumps(valid_items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"🧹 [Auto-Cleanup] Đã dọn dẹp {deleted_count} file audio lịch sử quá {max_age_hours}h. Kho giọng clone giữ nguyên vẹn!")

    return deleted_count


def enrich_history_item(it: dict, now: datetime) -> dict:
    """Tính toán thời gian còn lại trước khi tự hủy sau 24h để hiển thị trên UI."""
    item_copy = dict(it)
    created_at_str = it.get("created_at")
    if created_at_str:
        try:
            dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            expire_dt = dt + timedelta(hours=24)
            item_copy["expires_at"] = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
            remaining_seconds = max(0, int((expire_dt - now).total_seconds()))
            item_copy["remaining_seconds"] = remaining_seconds
            hours = remaining_seconds // 3600
            minutes = (remaining_seconds % 3600) // 60
            if hours > 0:
                item_copy["remaining_label"] = f"{hours}h {minutes}m"
            else:
                item_copy["remaining_label"] = f"{minutes}m"
        except Exception:
            item_copy["remaining_seconds"] = 86400
            item_copy["remaining_label"] = "24h"
    return item_copy


def save_to_history(filename: str, title: str, service: str, voice: str, duration_s: float, text: str, elapsed_s: float):
    items = []
    if HISTORY_INDEX.exists():
        try:
            items = json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
        except Exception:
            items = []

    now = datetime.now()
    expire_dt = now + timedelta(hours=24)
    new_item = {
        "id": uuid.uuid4().hex[:10],
        "filename": filename,
        "title": title,
        "service": service,
        "voice": voice,
        "duration_s": round(duration_s, 2),
        "text": text[:200] + ("..." if len(text) > 200 else ""),
        "elapsed_s": round(elapsed_s, 2),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": expire_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }
    items.insert(0, new_item)
    items = items[:100]
    HISTORY_INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_item


async def periodic_history_cleanup_loop():
    """Tác vụ nền chạy ngầm định kỳ mỗi 10 phút kiểm tra dọn dẹp file cũ > 24h."""
    while True:
        try:
            await asyncio.sleep(600)  # 10 phút kiểm tra một lần
            cleanup_expired_history(max_age_hours=24.0)
        except Exception as e:
            print(f"⚠️ Lỗi vòng lặp dọn dẹp lịch sử: {e}")


@app.on_event("startup")
async def on_startup():
    get_tts()
    cleanup_expired_history(max_age_hours=24.0)
    asyncio.create_task(periodic_history_cleanup_loop())


@app.get("/")
async def index():
    if CLIENT_HTML_PATH.exists():
        return FileResponse(CLIENT_HTML_PATH, media_type="text/html")
    return Response("unified.html not found", status_code=404)


@app.get("/favicon.ico")
async def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y="82" font-size="82">⚡</text></svg>'
    return Response(svg, media_type="image/svg+xml")


# --- HISTORY APIS ---
@app.get("/api/history")
async def get_history():
    cleanup_expired_history(max_age_hours=24.0)
    if HISTORY_INDEX.exists():
        try:
            items = json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
            now = datetime.now()
            return [enrich_history_item(it, now) for it in items]
        except Exception:
            return []
    return []


@app.get("/api/history/audio/{filename}")
async def get_history_audio(filename: str):
    file_path = OUTPUTS_DIR / filename
    if file_path.exists():
        return FileResponse(file_path, media_type="audio/wav")
    raise HTTPException(status_code=404, detail="File không tồn tại")


@app.delete("/api/history/{item_id}")
async def delete_history_item(item_id: str):
    if not HISTORY_INDEX.exists():
        return {"ok": True}
    items = json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
    filtered = []
    for it in items:
        if it["id"] == item_id:
            fp = OUTPUTS_DIR / it["filename"]
            if fp.exists():
                try:
                    os.remove(fp)
                except Exception:
                    pass
        else:
            filtered.append(it)
    HISTORY_INDEX.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "message": "Đã xóa audio lịch sử. Kho giọng clone vẫn được bảo toàn vĩnh viễn!"}


# --- 1. SERVICE: VOICES LIST ---
@app.get("/api/voices")
async def get_voices():
    tts = get_tts()
    try:
        raw_list = tts.list_preset_voices()
        result = []
        custom_names = set()

        # Check custom voices
        if CUSTOM_VOICES_FILE.exists():
            try:
                cvs = json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
                for cv in cvs:
                    custom_names.add(cv["name"])
                    is_ja = cv.get("language") in ("ja", "japanese", "tiếng nhật")
                    tag = "Tiếng Nhật" if is_ja else "Tiếng Việt"
                    result.append({
                        "id": cv["name"],
                        "name": f"⭐ {cv['name']} — Giọng của bạn ({tag})",
                        "is_custom": True,
                        "language": "ja" if is_ja else "vi",
                        "gender": cv.get("gender", "")
                    })
            except Exception:
                pass

        for item in raw_list:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                label, vid = item
                if vid not in custom_names:
                    result.append({"id": vid, "name": label, "is_custom": False, "language": "vi"})
            else:
                s_id = str(item)
                if s_id not in custom_names:
                    result.append({"id": s_id, "name": s_id, "is_custom": False, "language": "vi"})
        return result
    except Exception as e:
        return [{"id": "Hải Đăng", "name": "Hải Đăng", "is_custom": False, "language": "vi"}]


def _pcm16(audio_f32: np.ndarray) -> bytes:
    return (np.asarray(audio_f32) * 32767).clip(-32768, 32767).astype(np.int16).tobytes()


# --- 2. SERVICE: STREAMING AUDIO FOR VOICE EXPLORER ---
@app.get("/api/stream")
async def stream_audio(text: str, voice_id: Optional[str] = None):
    # Check if voice_id is Japanese custom voice or contains Japanese characters
    custom_voice = None
    if CUSTOM_VOICES_FILE.exists():
        try:
            cvs = json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
            custom_voice = next((x for x in cvs if x["name"] == voice_id), None)
        except Exception:
            pass

    is_ja_voice = custom_voice and custom_voice.get("language") in ("ja", "japanese", "tiếng nhật")
    has_ja_chars = any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9faf' for c in text)

    if (is_ja_voice and custom_voice and os.path.exists(custom_voice.get("ref_path", ""))) or (has_ja_chars and is_ja_voice):
        xtts = get_xtts()
        speaker_ref = custom_voice["ref_path"]
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_out = tmp.name
        try:
            xtts.tts_to_file(
                text=text,
                speaker_wav=speaker_ref,
                language="ja",
                file_path=tmp_out
            )
            audio, sr = sf.read(tmp_out)
            if sr != SAMPLE_RATE:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)

            def ja_generator():
                h = io.BytesIO()
                with wave.open(h, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(SAMPLE_RATE)
                    w.setnframes(len(audio))
                yield h.getvalue()
                yield _pcm16(audio)

            return StreamingResponse(ja_generator(), media_type="audio/wav")
        finally:
            if os.path.exists(tmp_out):
                try:
                    os.remove(tmp_out)
                except Exception:
                    pass

    tts = get_tts()

    def generator():
        h = io.BytesIO()
        with wave.open(h, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.setnframes(1_000_000_000)
        yield h.getvalue()

        for chunk in tts.infer_stream(text, voice=voice_id or None):
            if chunk is not None and len(chunk) > 0:
                yield _pcm16(chunk)

    return StreamingResponse(generator(), media_type="audio/wav")


# --- 3. SERVICE: FULL TEXT TO SPEECH (TTS) ---
class TTSRequest(BaseModel):
    text: str
    voice_id: str
    speed: float = 1.0


@app.post("/api/tts")
async def synthesize_tts(req: TTSRequest):
    t = req.text.strip()
    if not t:
        raise HTTPException(status_code=400, detail="Văn bản không được để trống")

    t0 = time.perf_counter()
    out_name = f"tts_{uuid.uuid4().hex[:8]}.wav"
    out_path = OUTPUTS_DIR / out_name

    # Check if custom voice is Japanese
    custom_voice = None
    if CUSTOM_VOICES_FILE.exists():
        try:
            cvs = json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
            custom_voice = next((x for x in cvs if x["name"] == req.voice_id), None)
        except Exception:
            pass

    is_ja_voice = custom_voice and custom_voice.get("language") in ("ja", "japanese", "tiếng nhật")
    has_ja_chars = any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9faf' for c in t)

    if (is_ja_voice and custom_voice and os.path.exists(custom_voice.get("ref_path", ""))) or (has_ja_chars and is_ja_voice):
        xtts = get_xtts()
        speaker_ref = custom_voice["ref_path"]
        xtts.tts_to_file(
            text=t,
            speaker_wav=speaker_ref,
            language="ja",
            file_path=str(out_path)
        )
        audio, out_sr = sf.read(str(out_path))
        sample_rate_used = out_sr
    else:
        tts = get_tts()
        audio = tts.infer(text=t, voice=req.voice_id)
        sample_rate_used = SAMPLE_RATE

        # Optional speed adjustment
        if req.speed != 1.0 and 0.5 <= req.speed <= 2.0:
            try:
                audio = librosa.effects.time_stretch(audio, rate=req.speed)
            except Exception:
                pass
        sf.write(str(out_path), audio, SAMPLE_RATE, format="WAV")

    elapsed_s = time.perf_counter() - t0
    dur_s = len(audio) / sample_rate_used

    save_to_history(
        filename=out_name,
        title=f"Đọc văn bản ({req.voice_id})",
        service="Text to Speech",
        voice=req.voice_id,
        duration_s=dur_s,
        text=t,
        elapsed_s=elapsed_s,
    )

    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate_used, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={
            "X-Elapsed-Seconds": f"{elapsed_s:.2f}",
            "X-Audio-Duration": f"{dur_s:.2f}",
            "Content-Disposition": f"attachment; filename={out_name}",
        },
    )


# --- 4. SERVICE: ZERO-SHOT VOICE CLONING (SAVE PERSISTENTLY) ---
@app.post("/api/clone")
async def clone_voice(
    audio_file: UploadFile = File(...),
    text: str = Form(...),
    voice_name: Optional[str] = Form(None),
    language: str = Form("vi"),
    save_as_preset: bool = Form(True),
    denoise: bool = Form(True),
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Văn bản cần đọc không được để trống")

    t0 = time.perf_counter()
    ext = Path(audio_file.filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
        content = await audio_file.read()
        tmp.write(content)

    try:
        lang = (language or "vi").strip().lower()
        out_name = f"cloned_{uuid.uuid4().hex[:8]}.wav"
        out_path = OUTPUTS_DIR / out_name

        if lang in ("ja", "japanese", "tiếng nhật"):
            xtts = get_xtts()
            xtts.tts_to_file(
                text=text,
                speaker_wav=tmp_path,
                language="ja",
                file_path=str(out_path),
            )
            audio, out_sr = sf.read(str(out_path))
            dur_s = len(audio) / out_sr
            sample_rate_used = out_sr
            elapsed_s = time.perf_counter() - t0
        else:
            tts = get_tts()
            audio = tts.infer(text=text, ref_audio=tmp_path, denoise=denoise)
            elapsed_s = time.perf_counter() - t0
            dur_s = len(audio) / SAMPLE_RATE
            sample_rate_used = SAMPLE_RATE
            sf.write(str(out_path), audio, SAMPLE_RATE, format="WAV")

        # Determine voice name
        v_name = voice_name.strip() if (voice_name and voice_name.strip()) else f"Clone_{uuid.uuid4().hex[:4]}"

        # Always save cloned reference audio permanently so it appears in Thư viện giọng đọc!
        perm_ref_path = REFS_DIR / f"{v_name}.wav"
        shutil.copyfile(tmp_path, str(perm_ref_path))

        # Register into tts active voices (for Vietnamese engine only)
        if lang not in ("ja", "japanese", "tiếng nhật"):
            try:
                tts_active = get_tts()
                tts_active.add_voice(
                    name=v_name,
                    ref_audio=str(perm_ref_path),
                    denoise=denoise,
                    description=f"Giọng clone (Tiếng Việt) từ {audio_file.filename}",
                    save=False,
                )
            except Exception as e:
                print(f"⚠️ add_voice warning: {e}")

        # Save to custom_voices.json
        custom_voices = []
        if CUSTOM_VOICES_FILE.exists():
            try:
                custom_voices = json.loads(CUSTOM_VOICES_FILE.read_text(encoding="utf-8"))
            except Exception:
                custom_voices = []
        # Update or append
        existing = next((x for x in custom_voices if x["name"] == v_name), None)
        voice_desc = f"Giọng clone ({'Tiếng Nhật' if lang in ('ja', 'japanese', 'tiếng nhật') else 'Tiếng Việt'}) từ {audio_file.filename}"
        if existing:
            existing["ref_path"] = str(perm_ref_path)
            existing["language"] = lang
        else:
            custom_voices.append({
                "name": v_name,
                "ref_path": str(perm_ref_path),
                "filename": audio_file.filename,
                "language": lang,
                "description": voice_desc,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        CUSTOM_VOICES_FILE.write_text(json.dumps(custom_voices, ensure_ascii=False, indent=2), encoding="utf-8")

        # Save to history
        save_to_history(
            filename=out_name,
            title=f"Nhân bản ({'Tiếng Nhật' if lang in ('ja', 'japanese', 'tiếng nhật') else 'Tiếng Việt'}): {v_name}",
            service="Voice Cloning",
            voice=v_name,
            duration_s=dur_s,
            text=text,
            elapsed_s=elapsed_s,
        )

        buf = io.BytesIO()
        sf.write(buf, audio, sample_rate_used, format="WAV")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers={
                "X-Elapsed-Seconds": f"{elapsed_s:.2f}",
                "X-Audio-Duration": f"{dur_s:.2f}",
                "X-Voice-Name": urllib.parse.quote(v_name),
                "Content-Disposition": f"attachment; filename={out_name}",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi clone giọng: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# --- 5. SERVICE: MULTI-SPEAKER CONVERSATION / PODCAST ---
class DialogueLine(BaseModel):
    speaker: str
    text: str


class PodcastRequest(BaseModel):
    lines: List[DialogueLine]
    pause_ms: int = 400


@app.post("/api/podcast")
async def generate_podcast(req: PodcastRequest):
    tts = get_tts()
    if not req.lines:
        raise HTTPException(status_code=400, detail="Kịch bản đối thoại không có nội dung")

    t0 = time.perf_counter()
    pause_samples = int(SAMPLE_RATE * (max(100, req.pause_ms) / 1000.0))
    silence = np.zeros(pause_samples, dtype=np.float32)

    clips = []
    full_text = []
    speakers_used = set()
    for line in req.lines:
        t = line.text.strip()
        if not t:
            continue
        spk = line.speaker.strip()
        speakers_used.add(spk)
        chunk = tts.infer(text=t, voice=spk)
        clips.append(chunk)
        clips.append(silence)
        full_text.append(f"[{spk}]: {t}")

    if not clips:
        raise HTTPException(status_code=400, detail="Không tạo được âm thanh từ kịch bản")

    final_audio = np.concatenate(clips[:-1]) if len(clips) > 1 else clips[0]
    elapsed_s = time.perf_counter() - t0
    dur_s = len(final_audio) / SAMPLE_RATE

    out_name = f"podcast_{uuid.uuid4().hex[:8]}.wav"
    sf.write(str(OUTPUTS_DIR / out_name), final_audio, SAMPLE_RATE, format="WAV")

    save_to_history(
        filename=out_name,
        title=f"Podcast ({len(req.lines)} lời thoại)",
        service="Podcast Studio",
        voice=", ".join(list(speakers_used)[:3]),
        duration_s=dur_s,
        text=" | ".join(full_text[:3]),
        elapsed_s=elapsed_s,
    )

    buf = io.BytesIO()
    sf.write(buf, final_audio, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={
            "X-Elapsed-Seconds": f"{elapsed_s:.2f}",
            "X-Audio-Duration": f"{dur_s:.2f}",
            "Content-Disposition": f"attachment; filename={out_name}",
        },
    )


# --- 6. SERVICE: SRT SUBTITLE DUBBING ---
@app.post("/api/srt")
async def srt_dubbing(srt_file: UploadFile = File(...), voice_id: str = Form(...)):
    tts = get_tts()
    t0 = time.perf_counter()
    content = await srt_file.read()
    text = content.decode("utf-8", errors="replace")

    cues = parse_srt(text)
    if not cues:
        raise HTTPException(status_code=400, detail="Không tìm thấy phụ đề hợp lệ trong file .srt")

    clips = []
    for cue in cues:
        w = tts.infer(text=cue.text, voice=voice_id)
        clips.append(w)

    track, pushed, max_shift = lay_on_timeline(clips, cues, SAMPLE_RATE)
    elapsed_s = time.perf_counter() - t0
    dur_s = len(track) / SAMPLE_RATE

    out_name = f"dubbed_{uuid.uuid4().hex[:8]}.wav"
    sf.write(str(OUTPUTS_DIR / out_name), track, SAMPLE_RATE, format="WAV")

    save_to_history(
        filename=out_name,
        title=f"Lồng tiếng SRT: {srt_file.filename}",
        service="SRT Dubbing",
        voice=voice_id,
        duration_s=dur_s,
        text=f"{len(cues)} đoạn phụ đề",
        elapsed_s=elapsed_s,
    )

    buf = io.BytesIO()
    sf.write(buf, track, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={
            "X-Elapsed-Seconds": f"{elapsed_s:.2f}",
            "X-Audio-Duration": f"{dur_s:.2f}",
            "Content-Disposition": f"attachment; filename={out_name}",
        },
    )


# --- 7. SERVICE: PDF TO AUDIOBOOK ---
@app.post("/api/pdf/extract")
async def extract_pdf(pdf_file: UploadFile = File(...)):
    if not HAS_FITZ:
        raise HTTPException(status_code=500, detail="PyMuPDF chưa được cài đặt")

    content = await pdf_file.read()
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        pages = []
        for p in doc:
            txt = p.get_text().strip()
            if txt:
                pages.append(txt)
        doc.close()
        return {"total_pages": len(pages), "pages": pages, "full_text": "\n\n".join(pages)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi đọc file PDF: {e}")


class PdfSpeechReq(BaseModel):
    text: str
    voice_id: str


@app.post("/api/pdf/synthesize")
async def synthesize_pdf_text(req: PdfSpeechReq):
    tts = get_tts()
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Văn bản rỗng")

    t0 = time.perf_counter()
    audio = tts.infer(text=req.text.strip(), voice=req.voice_id)
    elapsed_s = time.perf_counter() - t0
    dur_s = len(audio) / SAMPLE_RATE

    out_name = f"audiobook_{uuid.uuid4().hex[:8]}.wav"
    sf.write(str(OUTPUTS_DIR / out_name), audio, SAMPLE_RATE, format="WAV")

    save_to_history(
        filename=out_name,
        title="Sách nói PDF",
        service="PDF to Speech",
        voice=req.voice_id,
        duration_s=dur_s,
        text=req.text[:200],
        elapsed_s=elapsed_s,
    )

    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={
            "X-Elapsed-Seconds": f"{elapsed_s:.2f}",
            "X-Audio-Duration": f"{dur_s:.2f}",
            "Content-Disposition": f"attachment; filename={out_name}",
        },
    )


# --- 8. OPENAI-COMPATIBLE SPEECH API ---
class OpenAISpeechRequest(BaseModel):
    model: str = "adragon-v3-turbo"
    input: str
    voice: Optional[str] = "Hải Đăng"
    response_format: Optional[str] = "wav"


@app.post("/v1/audio/speech")
async def openai_speech(req: OpenAISpeechRequest):
    tts = get_tts()
    audio = tts.infer(text=req.input, voice=req.voice)
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return StreamingResponse(buf, media_type="audio/wav")


def main():
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n🚀 Adragon Studio Multi-Service Hub đang chạy tại: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
