"""STEP 3 — 用 ffmpeg 合併 MP3，並把完整語音 + logo 合成 MP4。"""
import os, shutil, subprocess
from .config import FFMPEG, MP3_OUTPUT_DIR, LOGO_FILE


def merge_mp3s(file_list, output_path):
    """用 ffmpeg concat 合併多個 MP3。回傳是否成功。"""
    if not file_list:
        print("[MERGE] No files to merge")
        return False
    if len(file_list) == 1:
        shutil.copy(file_list[0], output_path)
        print(f"[MERGE] Single file copied → {output_path}")
        return True

    list_path = os.path.join(MP3_OUTPUT_DIR, "_concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for fp in file_list:
            f.write(f"file '{fp}'\n")

    print(f"[MERGE] Merging {len(file_list)} files → {os.path.basename(output_path)}")
    result = subprocess.run(
        [FFMPEG, "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path, "-y"],
        capture_output=True, text=True, timeout=120,
    )
    try:
        os.remove(list_path)
    except Exception:
        pass

    if result.returncode != 0:
        print(f"[MERGE] ffmpeg error:\n{result.stderr}")
        return False
    print(f"[MERGE] Done! {os.path.basename(output_path)}: {os.path.getsize(output_path)/1024:.1f} KB")
    return True


def make_video(audio_path, output_path, image_path=LOGO_FILE):
    """把單張靜態圖 image_path + audio_path 合成 MP4。回傳是否成功。"""
    if not os.path.exists(audio_path):
        print(f"[VIDEO] Audio not found: {audio_path}")
        return False
    if not os.path.exists(image_path):
        print(f"[VIDEO] Logo image not found: {image_path}")
        return False

    print(f"[VIDEO] Building MP4: {os.path.basename(output_path)}")
    result = subprocess.run(
        [FFMPEG,
         "-loop", "1", "-i", image_path,
         "-i", audio_path,
         "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
         # 確保寬高為偶數，yuv420p 必要
         "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
         "-c:a", "aac", "-b:a", "192k",
         "-shortest", output_path, "-y"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        print(f"[VIDEO] ffmpeg error:\n{result.stderr}")
        return False
    print(f"[VIDEO] Done! {os.path.basename(output_path)}: {os.path.getsize(output_path)/1024/1024:.1f} MB")
    return True
