# storypilot_core.py (FINAL, STABLE VERSION)

import os
import re
import uuid
import json
import math
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from textwrap import wrap

from gtts import gTTS # Pro Voice Fix

# --- Folder Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# --- FFMPEG Path (Local PC) ---
FFMPEG_PATH = os.path.join(BASE_DIR, "ffmpeg.exe")
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = "ffmpeg" 

# --- Font file for captions ---
FONT_FILE = os.path.join(BASE_DIR, "BebasNeue-Regular.ttf")
if not os.path.exists(FONT_FILE):
    print("WARNING: Font file 'BebasNeue-Regular.ttf' not found. Using default.")
    FONT_FILE = "Arial.ttf" # Fallback font

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(os.path.join(UPLOADS_DIR, "images"), exist_ok=True)
os.makedirs(os.path.join(UPLOADS_DIR, "audio"), exist_ok=True)


@dataclass
class Prompt:
    number: int
    text: str

@dataclass
class AnalysisResult:
    scene_texts: List[str] = field(default_factory=list)
    copyable_prompts_text: str = ""
    full_story_for_voice: str = ""
    scene_count: int = 0

def generate_prompts_from_story(story_text: str) -> AnalysisResult:
    """
    Simple prompt generator. Splits story by newline or period.
    """
    print("Using standard prompt generator (non-Gemini).")
    scenes = [s.strip() for s in re.split(r'[\n\.]+', story_text) if s.strip() and len(s) > 5]
    if not scenes: scenes = [story_text] if story_text.strip() else []
    
    prompts = []
    copyable_lines = []
    full_voice_text_parts = []
    
    for i, scene_text in enumerate(scenes, 1):
        prompt_text = f"A cinematic 3D render of: {scene_text[:150]}"
        copyable_lines.append(f"{i}. {prompt_text}")
        full_voice_text_parts.append(scene_text)

    return AnalysisResult(
        scene_texts=scenes,
        copyable_prompts_text="\n".join(copyable_lines),
        full_story_for_voice=". ".join(full_voice_text_parts),
        scene_count=len(scenes)
    )

def map_images_by_prompt_number(image_files: List[str]) -> Dict[int, List[str]]:
    prompt_map = defaultdict(list)
    filename_regex = re.compile(r"^(\d+)") 
    
    for img_path in image_files:
        filename = os.path.basename(img_path)
        match = filename_regex.match(filename)
        
        if match:
            file_number = int(match.group(1))
            prompt_number = math.ceil(file_number / 2)
            prompt_map[prompt_number].append(img_path)
        else:
            print(f"Warning: Skipping file (no leading number): {filename}")

    sorted_map = dict(sorted(prompt_map.items()))
    return sorted_map

def generate_voice_fallback(text: str, gender: str, job_id: str) -> str:
    """
    Fallback TTS using gTTS (Google Translate's voice) - much better quality.
    """
    try:
        output_filename = f"voice_{job_id}.mp3"
        output_path = os.path.join(UPLOADS_DIR, "audio", output_filename)
        
        tts = gTTS(text=text, lang='en', slow=False)
        tts.save(output_path)
        
        print(f"gTTS voice generated successfully at {output_path}")
        return output_path
    except Exception as e:
        print(f"Error during gTTS generation: {e}")
        return ""

def get_audio_duration(file_path: str) -> float:
    """Gets the audio file duration in seconds using ffprobe."""
    try:
        ffprobe_path = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe")
        if "ffprobe" not in ffprobe_path:
             ffprobe_path = "ffprobe"
             
        cmd = [
            ffprobe_path, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting audio duration (is ffprobe installed?): {e}")
        return 0.0

def create_video_from_prompts(
    prompt_image_map: Dict[int, List[str]], 
    voice_file_path: str,
    job_id: str,
    target_duration_minutes: Optional[float] = None,
    scene_texts_for_caption: List[str] = []
) -> str:
    """
    Creates the final video with stable Zoom/Pan effects and synced Captions.
    """
    
    if target_duration_minutes:
        total_duration_sec = target_duration_minutes * 60
        print(f"Using Target Duration: {total_duration_sec}s")
    else:
        total_duration_sec = get_audio_duration(voice_file_path)
        print(f"Using Auto Duration (Voice): {total_duration_sec}s")

    if total_duration_sec == 0:
        raise Exception("Audio duration is 0. Upload a voice file or set a Target Duration.")

    num_prompts = len(prompt_image_map)
    if num_prompts == 0:
        raise Exception("No prompts with images found (Check image filenames).")

    duration_per_prompt = total_duration_sec / num_prompts
    
    video_segments = []
    segment_list_file = os.path.join(OUTPUT_DIR, f"segments_{job_id}.txt")
    segment_file_paths = [] 
    
    for i, prompt_num in enumerate(sorted(prompt_image_map.keys())):
        images = prompt_image_map[prompt_num]
        if not images: continue
            
        num_images_in_prompt = len(images)
        if num_images_in_prompt == 0: continue
            
        duration_per_image = max(0.5, duration_per_prompt / num_images_in_prompt)
        
        # --- Caption preparation ---
        current_scene_text = ""
        if (prompt_num - 1) < len(scene_texts_for_caption):
            current_scene_text = scene_texts_for_caption[prompt_num - 1]
        
        wrapped_text = "\n".join(wrap(current_scene_text, 40))
        caption_text = wrapped_text.replace("'", "'\\''").replace(":", "\:")
        
        for j, img_path in enumerate(images):
            segment_path = os.path.join(OUTPUT_DIR, f"{job_id}_seg_{i}_{j}.mp4")
            
            fps = 30
            zoom_duration_frames = int(duration_per_image * fps) + 1
            
            # --- STABLE ZOOM/PAN LOGIC (3-part Cycle) ---
            effect_cycle = (i * num_images_in_prompt + j) % 3

            if effect_cycle == 0: # 1st Pic: Zoom In (Focus Center)
                zoom_dir = "min(1.5,zoom+0.0025)"
                pan_x = "iw/2-(iw/zoom/2)"
                pan_y = "ih/2-(ih/zoom/2)"
            elif effect_cycle == 1: # 2nd Pic: Zoom Out (Focus Center)
                zoom_dir = f"max(1.0, 1.4 - 0.4*on/{zoom_duration_frames})"
                pan_x = "iw/2-(iw/zoom/2)"
                pan_y = "ih/2-(ih/zoom/2)"
            else: # 3rd Pic: Pan Left to Right (Stable Zoom)
                zoom_dir = "1.1" # Fixed slight zoom
                pan_x = f"if(eq(on,0), 0, x+((iw/zoom)/{zoom_duration_frames}/2))"
                pan_y = "y"

            vf_complex = (
                f"scale=1920:1080:force_original_aspect_ratio=decrease,"
                f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"zoompan=z='{zoom_dir}':x='{pan_x}':y='{pan_y}':"
                f"d={zoom_duration_frames}:s=1920x1080:fps={fps}"
            )
            
            # --- CAPTION FIX: Removed alpha animation for stability ---
            font_path_for_ffmpeg = FONT_FILE.replace(":", "\\:")
            
            vf_text_overlay = (
                f"drawtext=fontfile='{font_path_for_ffmpeg}':text='{caption_text}':"
                f"fontcolor=white:fontsize=60:x=(w-text_w)/2:y=h-th-50:"
                f"box=1:boxcolor=black@0.5:boxborderw=10"
            )

            cmd = [
                FFMPEG_PATH, '-y',
                '-loop', '1', '-i', img_path,
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                '-t', str(duration_per_image),
                '-vf', f"{vf_complex},{vf_text_overlay}",
                '-r', str(fps),
                segment_path
            ]
            
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                video_segments.append(segment_path)
                segment_file_paths.append(os.path.abspath(segment_path).replace(os.sep, '/'))
            except Exception as e:
                print(f"Error creating segment {segment_path}: {e}")

    try:
        with open(segment_list_file, 'w') as f:
            for path in segment_file_paths:
                f.write(f"file '{path}'\n")
    except Exception as e:
        raise Exception(f"Error writing segment list file: {e}")

    concatenated_video_path = os.path.join(OUTPUT_DIR, f"video_no_audio_{job_id}.mp4")
    cmd_concat = [
        FFMPEG_PATH, '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', segment_list_file,
        '-c', 'copy',
        concatenated_video_path
    ]
    subprocess.run(cmd_concat, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    final_output_path = os.path.join(OUTPUT_DIR, f"final_video_{job_id}.mp4")
    cmd_add_audio = [
        FFMPEG_PATH, '-y',
        '-i', concatenated_video_path,
        '-i', voice_file_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-shortest',
        final_output_path
    ]
    subprocess.run(cmd_add_audio, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        os.remove(segment_list_file)
        os.remove(concatenated_video_path)
        for seg in video_segments:
            os.remove(seg)
    except Exception as e:
        print(f"Warning: Could not clean up temp files: {e}")

    return final_output_path