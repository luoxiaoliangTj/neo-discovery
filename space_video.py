#!/usr/bin/env python3
"""Generate an animated space video with edge-tts narration and pillow graphics"""

import subprocess
import os
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def generate_space_video(text, output_path="space_video.mp4"):
    """Generate a space-themed animated video with narration"""
    
    # Step 1: Generate audio
    audio_path = "/tmp/space_audio.mp3"
    print("🎙️ Generating narration audio...")
    result = subprocess.run(
        ["edge-tts", "--voice", "zh-CN-XiaoxiaoNeural", "--rate=-15%", "--text", text, "--write-media", audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Audio failed: {result.stderr}")
        return None
    
    # Get audio duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(probe.stdout.strip())
    total_frames = int(audio_duration * 24)  # 24 fps
    print(f"📹 Generating {total_frames} frames at 24fps ({audio_duration:.1f}s)...")
    
    # Step 2: Generate animated frames
    frames_dir = "/tmp/space_frames"
    os.makedirs(frames_dir, exist_ok=True)
    
    # Try to find a good font
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
                font = ImageFont.truetype(fp, 36)
                break
            except:
                continue
    if font is None:
        font = ImageFont.load_default()
    
    try:
        small_font = font.font_variant(size=24)
    except:
        small_font = font
    
    width, height = 1280, 720
    
    for frame_num in range(total_frames):
        t = frame_num / 24.0  # time in seconds
        img = Image.new('RGB', (width, height), color=(5, 8, 22))
        draw = ImageDraw.Draw(img)
        
        # Draw starfield
        import random
        random.seed(42)
        for _ in range(200):
            x = random.randint(0, width-1)
            y = random.randint(0, height-1)
            brightness = random.randint(100, 255)
            size = random.choice([1, 1, 1, 2])
            draw.ellipse([x, y, x+size, y+size], fill=(brightness, brightness, brightness))
        
        # Draw moving asteroid (pulsing circle)
        ast_x = int(200 + 100 * math.sin(t * 0.8))
        ast_y = int(360 + 50 * math.cos(t * 0.6))
        ast_size = int(15 + 5 * math.sin(t * 3))
        # Glow
        for r in range(ast_size + 20, ast_size, -2):
            alpha = max(0, 100 - (r - ast_size) * 5)
            draw.ellipse([ast_x - r, ast_y - r, ast_x + r, ast_y + r], 
                         fill=(255, 100, 0 + alpha // 2))
        # Core
        draw.ellipse([ast_x - ast_size, ast_y - ast_size, ast_x + ast_size, ast_y + ast_size],
                     fill=(255, 120, 50))
        
        # Draw Earth (blue circle with detail)
        earth_x, earth_y = 900, 400
        earth_r = 60
        draw.ellipse([earth_x - earth_r, earth_y - earth_r, earth_x + earth_r, earth_y + earth_r],
                     fill=(30, 100, 200))
        # Continents (simple blobs)
        draw.ellipse([earth_x - 20, earth_y - 30, earth_x + 10, earth_y + 10], fill=(50, 150, 50))
        draw.ellipse([earth_x + 20, earth_y - 10, earth_x + 40, earth_y + 20], fill=(50, 150, 50))
        
        # Draw orbit path
        for angle in range(0, 360, 2):
            rad = math.radians(angle)
            ox = 640 + int(300 * math.cos(rad))
            oy = 360 + int(150 * math.sin(rad))
            if 0 <= ox < width and 0 <= oy < height:
                draw.ellipse([ox-1, oy-1, ox+1, oy+1], fill=(60, 80, 120))
        
        # Draw small probe moving along orbit
        probe_angle = (t * 30) % 360
        probe_rad = math.radians(probe_angle)
        px = 640 + int(300 * math.cos(probe_rad))
        py = 360 + int(150 * math.sin(probe_rad))
        draw.ellipse([px-4, py-4, px+4, py+4], fill=(200, 200, 255))
        # Trail
        for i in range(1, 6):
            trail_angle = probe_angle - i * 3
            trail_rad = math.radians(trail_angle)
            tx = 640 + int(300 * math.cos(trail_rad))
            ty = 360 + int(150 * math.sin(trail_rad))
            alpha = max(0, 255 - i * 40)
            draw.ellipse([tx-2, ty-2, tx+2, ty+2], fill=(100, 100, alpha))
        
        # Title text with glow
        title = "小行星监测系统"
        draw.text((50, 30), title, fill=(100, 200, 255), font=font)
        
        # Subtitle
        subtitle = f"NEO Tracker v2.0 | Frame {frame_num}"
        draw.text((50, 75), subtitle, fill=(150, 150, 180), font=small_font)
        
        # Progress bar
        progress = frame_num / total_frames
        draw.rectangle([50, height-30, width-50, height-15], outline=(60, 80, 120))
        bar_width = int((width - 100) * progress)
        draw.rectangle([50, height-30, 50 + bar_width, height-15], fill=(59, 130, 246))
        
        # Save frame
        img.save(f"{frames_dir}/frame_{frame_num:05d}.png", optimize=True)
    
    # Step 3: Combine frames + audio into video
    print("🎬 Encoding final video...")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-framerate", "24",
        "-i", f"{frames_dir}/frame_%05d.png",
        "-i", audio_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        output_path
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Encoding failed: {result.stderr}")
        return None
    
    # Cleanup frames
    subprocess.run(["rm", "-rf", frames_dir])
    
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"✅ Video saved: {output_path} ({size_mb:.1f} MB)")
    return output_path

if __name__ == "__main__":
    text = """你好！我是小行星监测系统。
    我们正在追踪一颗编号为 2026-AB1 的近地天体。
    这颗小行星直径约 200 米，正以每秒 15 公里的速度向地球飞来。
    别担心，它距离地球还有 300 万公里，不会造成威胁。
    我们的望远镜会持续监测它的轨道变化。
    下次再见！"""
    generate_space_video(text, "asteroid_monitor.mp4")
