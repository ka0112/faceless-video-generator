import os
import json
import time
import random
import asyncio
import requests
import urllib.parse
import streamlit as st
import json_repair
from PIL import Image

# PIL / MoviePy compatibility monkeypatch for Pillow 10+
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from huggingface_hub import InferenceClient
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips
from duckduckgo_search import DDGS

# Extract HF token from Streamlit Secrets
HF_TOKEN = st.secrets["HF_TOKEN"]
client = InferenceClient(token=HF_TOKEN)

NICHE_CONFIGS = {
    "Geopolitics": {
        "voice": "en-GB-RyanNeural",
        "style_suffix": ", gritty cinematic documentary style, dark atmospheric lighting, photorealistic",
        "system_prompt": "You are a top geopolitical analyst."
    },
    "Marketing": {
        "voice": "en-US-BrianNeural",
        "style_suffix": ", modern sleek corporate illustration, technology startup aesthetic, high contrast",
        "system_prompt": "You are an elite growth marketer."
    }
}

ASPECT_RATIOS = {
    "16:9 (Landscape - YouTube)": (1280, 720),
    "9:16 (Vertical - Shorts / Reels)": (720, 1280),
    "1:1 (Square)": (800, 800)
}

BGM_MOOD_TRACKS = {
    "Tense / Dramatic": "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3?filename=documentary-cinematic-112727.mp3",
    "Upbeat Business": "https://cdn.pixabay.com/download/audio/2022/03/15/audio_c8c8a8802d.mp3?filename=corporate-ambient-110241.mp3",
    "Suspenseful Investigation": "https://cdn.pixabay.com/download/audio/2021/09/06/audio_8b2111c13d.mp3?filename=investigation-background-11202.mp3"
}

def prepare_image_aspect(image_path, target_w, target_h):
    """Crop and resize any image to fit target aspect ratio perfectly."""
    try:
        img = Image.open(image_path).convert("RGB")
        img_ratio = img.width / img.height
        target_ratio = target_w / target_h

        if img_ratio > target_ratio:
            new_w = int(img.height * target_ratio)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        else:
            new_h = int(img.width / target_ratio)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))

        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        img.save(image_path)
    except Exception as e:
        print(f"Aspect formatting warning: {e}")

def fetch_wikimedia_image(query, target_w, target_h, output_path):
    """Searches Wikimedia Commons API for real photos and charts."""
    try:
        url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",
            "gsrlimit": "5",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "format": "json"
        }
        res = requests.get(url, params=params, timeout=6, headers={"User-Agent": "FacelessEngineBot/1.0"})
        if res.status_code == 200:
            data = res.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                imageinfo = page.get("imageinfo", [])
                if imageinfo:
                    img_url = imageinfo[0].get("url")
                    mime = imageinfo[0].get("mime", "")
                    if img_url and ("image/jpeg" in mime or "image/png" in mime or "image/webp" in mime):
                        r = requests.get(img_url, timeout=8, headers={"User-Agent": "FacelessEngineBot/1.0"})
                        if r.status_code == 200 and len(r.content) > 6000:
                            with open(output_path, 'wb') as f:
                                f.write(r.content)
                            prepare_image_aspect(output_path, target_w, target_h)
                            return True
    except Exception as e:
        print(f"Wikimedia search notice for '{query}': {e}")
    return False

def fetch_ddg_image(query, target_w, target_h, output_path):
    """Searches web photos via DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=6))
            for res in results:
                img_url = res.get('image')
                if not img_url:
                    continue
                r = requests.get(img_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                if r.status_code == 200 and len(r.content) > 8000:
                    with open(output_path, 'wb') as f:
                        f.write(r.content)
                    prepare_image_aspect(output_path, target_w, target_h)
                    return True
    except Exception as e:
        print(f"DDG search notice for '{query}': {e}")
    return False

def fetch_pollinations_image(prompt, target_w, target_h, output_path):
    """Fallback free AI image generator."""
    try:
        clean_prompt = urllib.parse.quote(prompt)
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{clean_prompt}?width={target_w}&height={target_h}&seed={seed}&nologo=true"
        r = requests.get(url, timeout=12)
        if r.status_code == 200 and len(r.content) > 5000:
            with open(output_path, 'wb') as f:
                f.write(r.content)
            prepare_image_aspect(output_path, target_w, target_h)
            return True
    except Exception as e:
        print(f"Pollinations notice: {e}")
    return False

def apply_dynamic_motion(clip, target_w, target_h, zoom_in=True):
    """Adds smooth alternating Zoom-In / Zoom-Out motion."""
    def zoom_fn(t):
        progress = t / max(clip.duration, 1)
        if zoom_in:
            return 1.0 + 0.18 * progress
        else:
            return 1.18 - 0.18 * progress

    zoomed = clip.resize(zoom_fn)
    return zoomed.crop(x_center=zoomed.w / 2, y_center=zoomed.h / 2, width=target_w, height=target_h)

async def generate_audio(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def build_video_pipeline(topic, detailed_context, niche, aspect_ratio_label, uploaded_files, selected_mood):
    config = NICHE_CONFIGS[niche]
    target_w, target_h = ASPECT_RATIOS[aspect_ratio_label]
    
    # Process custom uploaded files
    custom_image_paths = []
    if uploaded_files:
        for idx, file in enumerate(uploaded_files):
            custom_path = f"custom_{idx}.jpg"
            with open(custom_path, "wb") as f:
                f.write(file.getbuffer())
            prepare_image_aspect(custom_path, target_w, target_h)
            custom_image_paths.append(custom_path)

    # 1. SCRIPT ENGINE: PRESERVE FULL CONTEXT LENGTH
    if detailed_context and detailed_context.strip():
        # Keep 100% of user text verbatim so voiceover length is maintained!
        full_narration = detailed_context.strip()
        
        prompt = f"""
        Analyze this video narration text and break it into 25 to 35 sequential visual scene search queries.
        NARRATION SAMPLE:
        {full_narration[:2000]}
        
        Respond ONLY with a raw JSON array of objects:
        [
          {{"scene_number": 1, "search_query": "LEGO company logo building", "ai_prompt": "LEGO corporate office exterior"}},
          {{"scene_number": 2, "search_query": "LEGO sales chart graph", "ai_prompt": "Financial chart showing toy sales growth"}}
        ]
        """
    else:
        prompt = f"""
        Write a detailed, long-form video script about: {topic}.
        Respond ONLY with a raw JSON object matching this structure:
        {{
          "full_narration": "Full narration text here...",
          "scenes": [
            {{"scene_number": 1, "search_query": "LEGO search query", "ai_prompt": "AI prompt"}}
          ]
        }}
        """

    try:
        completion = client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[
                {"role": "system", "content": config["system_prompt"] + " Return strictly raw JSON with 25-35 visual scenes."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=3000
        )
        raw_text = completion.choices[0].message.content.strip()
        parsed_data = json_repair.loads(raw_text)
        
        if detailed_context and detailed_context.strip():
            # If user provided context, narration is preserved verbatim
            if isinstance(parsed_data, list):
                scenes = parsed_data
            elif isinstance(parsed_data, dict) and "scenes" in parsed_data:
                scenes = parsed_data["scenes"]
            else:
                scenes = [{"scene_number": i+1, "search_query": topic or "LEGO", "ai_prompt": topic or "LEGO"} for i in range(25)]
        else:
            full_narration = parsed_data.get("full_narration", "")
            scenes = parsed_data.get("scenes", [])
            
    except Exception as e:
        return None, f"Script Generation Error: {str(e)}"

    # 2. VOICE OVER GENERATION (FULL LENGTH)
    try:
        audio_path = "narration.mp3"
        asyncio.run(generate_audio(full_narration, config["voice"], audio_path))
        
        narration_clip = AudioFileClip(audio_path)
        total_duration = narration_clip.duration
        scene_duration = total_duration / len(scenes)
    except Exception as e:
        return None, f"Audio Generation Error: {str(e)}"

    # 3. BACKGROUND MUSIC ENGINE
    try:
        bgm_path = "bgm.mp3"
        bgm_url = BGM_MOOD_TRACKS[selected_mood]
        res = requests.get(bgm_url, timeout=15)
        with open(bgm_path, "wb") as f:
            f.write(res.content)

        bgm_clip = AudioFileClip(bgm_path)
        if bgm_clip.duration < total_duration:
            bgm_clip = bgm_clip.loop(duration=total_duration)
        else:
            bgm_clip = bgm_clip.subclip(0, total_duration)
            
        bgm_clip = bgm_clip.volumex(0.10) # 10% volume
        combined_audio = CompositeAudioClip([narration_clip, bgm_clip])
    except Exception as e:
        combined_audio = narration_clip

    # 4. QUAD-LAYER VISUAL RETRIEVAL (CUSTOM -> WIKIMEDIA -> DDG -> POLLINATIONS)
    image_clips = []
    main_subject = topic if topic else "LEGO bricks strategy"
    
    try:
        for i, scene in enumerate(scenes):
            img_path = f"scene_{i}.jpg"
            image_retrieved = False
            query = scene.get("search_query", main_subject)
            
            # Priority 1: User Uploaded Custom Images
            if custom_image_paths and (i % 3 == 0 or i >= len(scenes) - len(custom_image_paths)):
                custom_selected = custom_image_paths[i % len(custom_image_paths)]
                img = Image.open(custom_selected)
                img.save(img_path)
                image_retrieved = True
                
            # Priority 2: Wikimedia Commons Search
            if not image_retrieved:
                image_retrieved = fetch_wikimedia_image(query, target_w, target_h, img_path)
                
            # Priority 3: DuckDuckGo Search
            if not image_retrieved:
                image_retrieved = fetch_ddg_image(query, target_w, target_h, img_path)

            # Priority 4: Free Pollinations AI Generation
            if not image_retrieved:
                ai_p = scene.get("ai_prompt", query) + config["style_suffix"]
                image_retrieved = fetch_pollinations_image(ai_p, target_w, target_h, img_path)

            # Priority 5: Main Subject Fallback (Guarantees NO pastel placeholders!)
            if not image_retrieved or not os.path.exists(img_path):
                fetch_wikimedia_image(main_subject, target_w, target_h, img_path)

            time.sleep(0.3)

            # Apply alternating camera motion
            zoom_in_direction = (i % 2 == 0)
            raw_clip = ImageClip(img_path).set_duration(scene_duration)
            motion_clip = apply_dynamic_motion(raw_clip, target_w, target_h, zoom_in=zoom_in_direction)
            image_clips.append(motion_clip)
            
    except Exception as e:
        return None, f"Visual Processing Error: {str(e)}"

    # 5. FINAL VIDEO ASSEMBLY
    try:
        final_video_path = "final_output.mp4"
        video = concatenate_videoclips(image_clips, method="compose")
        video = video.set_audio(combined_audio)
        video.write_videofile(final_video_path, fps=15, codec="libx264", audio_codec="aac")
        return final_video_path, full_narration
            
    except Exception as e:
        return None, f"Video Compilation Error: {str(e)}"

# STREAMLIT UI
st.set_page_config(page_title="Faceless Video Engine Pro", layout="wide")
st.title("🎬 Faceless Video Engine Pro")
st.subheader("Full-Length Video Pipeline: Uncompressed Narration + Real Web Photos + BGM")

col1, col2 = st.columns([1, 1])

with col1:
    topic_input = st.text_input("Option A: Short Topic Idea", placeholder="e.g., How LEGO uses scarcity marketing")
    context_input = st.text_area("Option B: Paste Detailed Context (Overrides Option A)", placeholder="Paste transcripts, articles, or notes here...", height=200)
    
    st.markdown("---")
    niche_dropdown = st.selectbox("Select Target Niche", ["Marketing", "Geopolitics"])
    selected_mood = st.selectbox("Select Contextual Audio Mood", list(BGM_MOOD_TRACKS.keys()), index=0)
    aspect_ratio = st.selectbox("Select Aspect Ratio", list(ASPECT_RATIOS.keys()), index=0)
    
    uploaded_files = st.file_uploader(
        "Upload Custom Images/Graphics (Optional)", 
        type=["png", "jpg", "jpeg"], 
        accept_multiple_files=True
    )
    
    submit_btn = st.button("Build High-Pacing Video", type="primary")

if submit_btn:
    with st.spinner("Processing full-length narration, pulling real photos, mixing BGM, and compiling video..."):
        video_file, transcript = build_video_pipeline(
            topic_input, context_input, niche_dropdown, aspect_ratio, uploaded_files, selected_mood
        )
        
        with col2:
            if video_file:
                st.success("Full-Length Asset Rendered Successfully!")
                st.video(video_file)
                st.text_area("Generated Script Transcript", value=transcript, height=200, disabled=True)
            else:
                st.error(transcript)
