#!/usr/bin/env python3
"""Text-to-video: generates a video from text using edge-tts + pillow + ffmpeg"""

import subprocess
import sys
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def text_to_video(text, output_path="output.mp4", duration=None):
    # Step 1: Generate audio using edge-tts
    audio_path = "/tmp/tts_audio.mp3"
    print(f"Generating audio for: {text[:50]}...")
    result = subprocess.run(
        ["edge-tts", "--voice", "zh-CN-XiaoxiaoNeural", "--text", text, "--write-media", audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Audio generation failed: {result.stderr}")
        return None
    
    # Get audio duration
    if duration is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
            capture_output=True, text=True
        )
        duration = float(probe.stdout.strip()) + 1
    
    # Step 2: Create image with text
    img = Image.new('RGB', (1280, 720), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)
    
    # Try to use a nice font, fall back to default
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    
    font = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 48)
                break
            except:
                continue
    
    if font is None:
        font = ImageFont.load_default()
    
    # Wrap text and draw
    words = text
    lines = []
    max_chars = 40
    for i in range(0, len(words), max_chars):
        lines.append(words[i:i+max_chars])
    
    y = 300
    for line in lines:
        draw.text((100, y), line, fill=(226, 232, 240), font=font)
        y += 60
    
    # Add a subtle accent line
    draw.line([(100, 280), (1180, 280)], fill=(59, 130, 246), width=2)
    
    img_path = "/tmp/text_image.png"
    img.save(img_path)
    print(f"Image created: {img_path}")
    
    # Step 3: Combine image + audio into video
    print(f"Creating video (duration: {duration:.1f}s)...")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        output_path
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Video creation failed: {result.stderr}")
        return None
    
    print(f"Video saved: {output_path}")
    return output_path

if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "你好，这是一个测试视频。"
    output = sys.argv[2] if len(sys.argv) > 2 else "output.mp4"
    text_to_video(text, output)
