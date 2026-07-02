#!/usr/bin/env python3
"""Generate a space monitoring dashboard video with animations"""

import subprocess
import os
import math
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def generate_dashboard_video(text, output_path="dashboard.mp4"):
    """Generate an animated space dashboard video"""
    
    # Step 1: Generate audio
    audio_path = "/tmp/dash_audio.mp3"
    print("🎙️ Generating narration...")
    result = subprocess.run(
        ["edge-tts", "--voice", "zh-CN-XiaoxiaoNeural", "--rate=-10%", "--text", text, "--write-media", audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Audio failed: {result.stderr}")
        return None
    
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(probe.stdout.strip())
    fps = 24
    total_frames = int(audio_duration * fps)
    print(f"📹 Generating {total_frames} frames at {fps}fps ({audio_duration:.1f}s)...")
    
    # Setup
    frames_dir = "/tmp/dash_frames"
    os.makedirs(frames_dir, exist_ok=True)
    
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    font = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 32)
                break
            except:
                continue
    if font is None:
        font = ImageFont.load_default()
    
    try:
        small_font = font.font_variant(size=20)
        title_font = font.font_variant(size=42)
    except:
        small_font = font
        title_font = font
    
    W, H = 1280, 720
    random.seed(42)
    
    # Pre-generate stars
    stars = [(random.randint(0, W-1), random.randint(0, H-1), random.uniform(0.5, 2.5), random.uniform(0.3, 1.0)) for _ in range(300)]
    
    # Pre-generate asteroid trail points
    asteroid_points = []
    for i in range(200):
        t = i / 200.0
        angle = t * math.pi * 4
        r = 200 + 50 * math.sin(t * 10)
        x = int(W/2 + r * math.cos(angle))
        y = int(H/2 + r * math.sin(angle) * 0.6)
        asteroid_points.append((x, y))
    
    for frame_num in range(total_frames):
        t = frame_num / fps
        
        # Create frame
        img = Image.new('RGB', (W, H), color=(8, 12, 30))
        draw = ImageDraw.Draw(img)
        
        # Draw animated starfield (twinkling)
        for sx, sy, base_size, brightness in stars:
            twinkle = 0.5 + 0.5 * math.sin(t * 3 + sx * 0.1 + sy * 0.1)
            size = base_size * twinkle
            alpha = int(brightness * twinkle * 255)
            draw.ellipse([sx-size, sy-size, sx+size, sy+size], fill=(alpha, alpha, min(255, alpha+50)))
        
        # Draw grid lines (radar style)
        grid_alpha = 30
        for i in range(0, W, 80):
            draw.line([(i, 0), (i, H)], fill=(0, grid_alpha, grid_alpha), width=1)
        for i in range(0, H, 80):
            draw.line([(0, i), (W, i)], fill=(0, grid_alpha, grid_alpha), width=1)
        
        # Draw radar circles
        for r in [100, 200, 300]:
            alpha = 40 + int(20 * math.sin(t * 2 + r * 0.01))
            draw.ellipse([W//2 - r, H//2 - r, W//2 + r, H//2 + r], outline=(0, alpha, alpha), width=1)
        
        # Draw scanning line
        scan_angle = (t * 60) % 360
        scan_rad = math.radians(scan_angle)
        scan_x = W//2 + int(350 * math.cos(scan_rad))
        scan_y = H//2 + int(350 * math.sin(scan_rad))
        draw.line([(W//2, H//2), (scan_x, scan_y)], fill=(0, 200, 100), width=2)
        
        # Draw scan trail
        for i in range(1, 30):
            trail_angle = scan_angle - i * 2
            trail_rad = math.radians(trail_angle)
            tx = W//2 + int(350 * math.cos(trail_rad))
            ty = H//2 + int(350 * math.sin(trail_rad))
            alpha = max(0, 200 - i * 7)
            draw.ellipse([tx-1, ty-1, tx+1, ty+1], fill=(0, alpha, alpha//2))
        
        # Draw moving asteroid (pulsing with orbit)
        ast_t = (t * 0.5) % 1.0
        ast_angle = ast_t * math.pi * 2
        ast_x = W//2 + int(200 * math.cos(ast_angle))
        ast_y = H//2 + int(120 * math.sin(ast_angle))
        ast_size = int(8 + 4 * math.sin(t * 5))
        
        # Asteroid glow
        for r in range(ast_size + 25, ast_size, -3):
            alpha = max(0, 80 - (r - ast_size) * 3)
            draw.ellipse([ast_x-r, ast_y-r, ast_x+r, ast_y+r], fill=(255, 100 + alpha//2, 0))
        
        # Asteroid core
        draw.ellipse([ast_x-ast_size, ast_y-ast_size, ast_x+ast_size, ast_y+ast_size], fill=(255, 140, 50))
        # Highlight
        draw.ellipse([ast_x-ast_size//2, ast_y-ast_size//2, ast_x+ast_size//3, ast_y+ast_size//3], fill=(255, 200, 150))
        
        # Draw Earth
        earth_x, earth_y = 950, 200
        earth_r = 50
        draw.ellipse([earth_x-earth_r, earth_y-earth_r, earth_x+earth_r, earth_y+earth_r], fill=(40, 100, 200))
        # Earth details
        draw.ellipse([earth_x-15, earth_y-20, earth_x+5, earth_y+10], fill=(60, 160, 60))
        draw.ellipse([earth_x+10, earth_y-10, earth_x+30, earth_y+15], fill=(60, 160, 60))
        # Atmosphere glow
        for r in range(earth_r + 15, earth_r, -2):
            alpha = max(0, 40 - (r - earth_r) * 2)
            draw.ellipse([earth_x-r, earth_y-r, earth_x+r, earth_y+r], outline=(100, 200, 255, alpha), width=2)
        
        # Draw second asteroid (smaller, different orbit)
        ast2_t = (t * 0.3 + 0.5) % 1.0
        ast2_x = W//2 + int(280 * math.cos(ast2_t * math.pi * 2 + 1))
        ast2_y = H//2 + int(160 * math.sin(ast2_t * math.pi * 2 + 1))
        ast2_size = int(5 + 2 * math.sin(t * 4))
        draw.ellipse([ast2_x-ast2_size, ast2_y-ast2_size, ast2_x+ast2_size, ast2_y+ast2_size], fill=(200, 180, 160))
        
        # Draw detection box around main asteroid
        box_size = ast_size + 30
        box_color = (0, 255, 100)
        # Corner brackets
        blen = 10
        bw = 2
        # Top-left
        draw.line([(ast_x-box_size, ast_y-box_size), (ast_x-box_size+blen, ast_y-box_size)], fill=box_color, width=bw)
        draw.line([(ast_x-box_size, ast_y-box_size), (ast_x-box_size, ast_y-box_size+blen)], fill=box_color, width=bw)
        # Top-right
        draw.line([(ast_x+box_size, ast_y-box_size), (ast_x+box_size-blen, ast_y-box_size)], fill=box_color, width=bw)
        draw.line([(ast_x+box_size, ast_y-box_size), (ast_x+box_size, ast_y-box_size+blen)], fill=box_color, width=bw)
        # Bottom-left
        draw.line([(ast_x-box_size, ast_y+box_size), (ast_x-box_size+blen, ast_y+box_size)], fill=box_color, width=bw)
        draw.line([(ast_x-box_size, ast_y+box_size), (ast_x-box_size, ast_y+box_size-blen)], fill=box_color, width=bw)
        # Bottom-right
        draw.line([(ast_x+box_size, ast_y+box_size), (ast_x+box_size-blen, ast_y+box_size)], fill=box_color, width=bw)
        draw.line([(ast_x+box_size, ast_y+box_size), (ast_x+box_size, ast_y+box_size-blen)], fill=box_color, width=bw)
        
        # Data readout (top-left)
        draw.rectangle([10, 10, 300, 120], fill=(0, 0, 0, 128))
        draw.text((20, 15), "NEO TRACKER v2.0", fill=(0, 255, 100), font=font)
        draw.text((20, 50), f"目标: 2026-AB1", fill=(200, 200, 200), font=small_font)
        draw.text((20, 75), f"距离: {200 + int(50*math.sin(t*0.5))}万km", fill=(200, 200, 200), font=small_font)
        draw.text((20, 100), f"速度: 15.{int(5*math.sin(t*2))} km/s", fill=(200, 200, 200), font=small_font)
        
        # Status bar (bottom)
        draw.rectangle([0, H-40, W, H], fill=(0, 0, 0, 180))
        draw.text((20, H-30), "● 在线监测中", fill=(0, 255, 100), font=small_font)
        draw.text((200, H-30), f"帧率: {fps} FPS", fill=(100, 200, 255), font=small_font)
        draw.text((380, H-30), f"时间: {t:.1f}s", fill=(100, 200, 255), font=small_font)
        draw.text((550, H-30), "状态: 正常", fill=(255, 200, 100), font=small_font)
        
        # Progress bar
        progress = frame_num / total_frames
        draw.rectangle([W-300, H-35, W-20, H-15], outline=(100, 100, 100))
        bar_w = int(280 * progress)
        draw.rectangle([W-300, H-35, W-300 + bar_w, H-15], fill=(0, 200, 100))
        
        # Save frame
        img.save(f"{frames_dir}/frame_{frame_num:05d}.png", optimize=True)
    
    # Step 3: Encode video
    print("🎬 Encoding video...")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", f"{frames_dir}/frame_%05d.png",
        "-i", audio_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        output_path
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Encoding failed: {result.stderr}")
        return None
    
    # Cleanup
    subprocess.run(["rm", "-rf", frames_dir])
    
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"✅ Done! {output_path} ({size_mb:.1f} MB)")
    return output_path

if __name__ == "__main__":
    text = """你好！我是小行星监测系统。
    我们正在追踪近地天体 2026-AB1。
    监测系统在线，状态正常。"""
    generate_dashboard_video(text, "asteroid_dashboard.mp4")
