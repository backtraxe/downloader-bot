import os
import subprocess
import glob
import math

def get_video_info(file_path):
    """获取视频时长"""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except: return None

def split_video(file_path, file_prefix):
    """如果视频大于 50MB，则切割"""
    file_size = os.path.getsize(file_path)
    limit_bytes = 49 * 1024 * 1024 # 49MB
    
    if file_size <= limit_bytes:
        return [file_path]

    print(f"🔧 正在切割视频: {file_path}")
    duration = get_video_info(file_path)
    if not duration: return [file_path]

    num_parts = math.ceil(file_size / limit_bytes)
    segment_time = int(duration / num_parts)
    if segment_time < 10: segment_time = 10
    
    output_pattern = f"{os.path.dirname(file_path)}/{file_prefix}_part%03d.mp4"
    
    cmd = [
        'ffmpeg', '-y', '-i', file_path, '-c', 'copy', '-map', '0', 
        '-f', 'segment', '-segment_time', str(segment_time), 
        '-reset_timestamps', '1', output_pattern
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        parts = sorted(glob.glob(f"{os.path.dirname(file_path)}/{file_prefix}_part*.mp4"))
        if parts:
            os.remove(file_path) # 删除原文件
            return parts
    except Exception as e:
        print(f"切割失败: {e}")
        
    return [file_path]
