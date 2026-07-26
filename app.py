import os
import json
import random
import asyncio
import requests
import urllib.parse
import streamlit as st
import json_repair
from PIL import Image
from duckduckgo_search import DDGS

# PIL / MoviePy compatibility monkeypatch for Pillow 10+
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from huggingface_hub import InferenceClient
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips

# Extract HF token from Streamlit Secrets for text generation
HF_TOKEN = st.secrets["HF_TOKEN"]
client = InferenceClient(token=HF_TOKEN)

NICHE_CONFIGS = {
    "Geopolitics": {
        "voice": "en-GB-RyanNeural",
        "style_suffix": ", gritty cinematic documentary style, dark atmospheric lighting, photorealistic",
        "system_prompt": "You are a top geopolitical analyst. Write a high-tension, fact-grounded documentary narrative."
    },
    "Marketing": {
        "voice": "en-US-BrianNeural",
        "style_suffix": ", modern sleek corporate illustration, technology startup aesthetic, high contrast",
        "system_prompt": "You are an elite growth marketer. Write a fast-paced, highly engaging business case study breakdown."
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

def fetch_real_web_image(query, target_w, target_h, output_path):
    """Searches real web and stock photos to ensure authentic, non-hallucinated visuals."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=8))
            for res in results:
                img_url = res.get('image')
                if not img_url:
                    continue
                r = requests.get(img_url, timeout=6, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                if r.status_code == 200 and len(r.content) > 10000:
                    with open(output_path, 'wb') as f:
                        f.write(r.content)
                    prepare_image_aspect(output_path, target_w, target_h)
                    return True
    except Exception as e:
        print(f"Web image search notice for '{query}': {e}")
    return False

def fetch_free_pollinations_image(prompt, target_w, target_h, output_path):
    """Fallback unlimited free AI generator completely bypassing Hugging Face credits."""
    try:
        clean_prompt = urllib.parse.quote(prompt)
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{clean_prompt}?width={target_w}&height={target_h}&seed={seed}&nologo=true&model=flux"
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            with open(output_path, 'wb') as f:
                f.write(r.content)
            prepare_image_aspect(output_path, target_w, target_h)
            return True
    except Exception as e:
        print(f"Pollinations fallback notice: {e}")
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
    
    # Process custom uploaded user files
    custom_image_paths = []
    if uploaded_files:
        for idx, file in enumerate(uploaded_files):
            custom_path = f"custom_{idx}.jpg"
            with open(custom_path, "wb") as f:
                f.write(file.getbuffer())
            prepare_image_aspect(custom_path, target_w, target_h)
            custom_image_paths.append(custom_path)

    # 1. SCRIPT GENERATION ENGINE (20 - 35 DISTINCT SCENES)
    prompt = f"""
    Adapt and condense the following source text or topic into a high-retention video script:
    TOPIC / SOURCE:
    {detailed_context if detailed_context and detailed_context.strip() else topic}
    
    CRITICAL INSTRUCTIONS FOR HIGH VISUAL PACING:
    1. Break the narration into 20 to 35 short, distinct sequential visual beats.
    2. For EVERY scene, specify:
       - 'search_query': A search string for a REAL web photo/chart/product (e.g. "LEGO company sales graph chart", "LEGO factory building", "Darth Vader Lego set").
       - 'ai_prompt': A creative description for AI generation if web search is offline.
    
    Respond ONLY with a raw JSON object matching this exact structure:
    {{
      "full_narration": "The complete narration text written seamlessly.",
      "scenes": [
        {{
          "scene_number": 1,
          "search_query": "real web search query here",
          "ai_prompt": "creative ai description here"
        }}
      ]
    }}
    """

    try:
        completion = client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[
                {"role": "system", "content": config["system_prompt"] + " Return strictly raw JSON. Force 20 to 35 scenes."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=4000
        )
        raw_text = completion.choices[0].message.content.strip()
        data = json_repair.loads(raw_text)
        
        if not isinstance(data, dict) or "full_narration" not in data or "scenes" not in data:
            raise ValueError("Output missing required keys.")
            
    except Exception as e:
        return None, f"Script Generation Error: {str(e)}"

    # 2. VOICE OVER GENERATION
    try:
        audio_path = "narration.mp3"
        asyncio.run(generate_audio(data["full_narration"], config["voice"], audio_path))
        
        narration_clip = AudioFileClip(audio_path)
        total_duration = narration_clip.duration
        scene_duration = total_duration / len(data["scenes"])
    except Exception as e:
        return None, f"Audio Generation Error: {str(e)}"

    # 3. CONTEXTUAL BACKGROUND MUSIC ENGINE
    try:
        bgm_path = "bgm.mp3"
        bgm_url = BGM_MOOD_TRACKS[selected_mood]
        res = requests.get(bgm_url, timeout=30)
        with open(bgm_path, "wb") as f:
            f.write(res.content)

        bgm_clip = AudioFileClip(bgm_path)
        if bgm_clip.duration < total_duration:
            bgm_clip = bgm_clip.loop(duration=total_duration)
        else:
            bgm_clip = bgm_clip.subclip(0, total_duration)
            
        bgm_clip = bgm_clip.volumex(0.10) # 10% soft volume
        combined_audio = CompositeAudioClip([narration_clip, bgm_clip])
    except Exception as e:
        combined_audio = narration_clip

    # 4. FREE HYBRID VISUAL RETRIEVAL (DDG SEARCH -> POLLINATIONS FALLBACK)
    image_clips = []
    try:
        for i, scene in enumerate(data["scenes"]):
            img_path = f"scene_{i}.jpg"
            image_retrieved = False
            
            # Priority 1: User Uploaded Custom Image
            if custom_image_paths and (i % 3 == 0 or i >= len(data["scenes"]) - len(custom_image_paths)):
                custom_selected = custom_image_paths[i % len(custom_image_paths)]
                img = Image.open(custom_selected)
                img.save(img_path)
                image_retrieved = True
                
            # Priority 2: Fetch Real Web / Stock Photo (DuckDuckGo)
            if not image_retrieved:
                query = scene.get("search_query", topic)
                image_retrieved = fetch_real_web_image(query, target_w, target_h, img_path)
                
            # Priority 3: Fallback to Unlimited Free Pollinations AI Endpoint
            if not image_retrieved:
                ai_p = scene.get("ai_prompt", topic) + config["style_suffix"]
                image_retrieved = fetch_free_pollinations_image(ai_p, target_w, target_h, img_path)

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
        return final_video_path, data["full_narration"]
            
    except Exception as e:
        return None, f"Video Compilation Error: {str(e)}"

# STREAMLIT UI
st.set_page_config(page_title="Faceless Video Engine Pro", layout="wide")
st.title("🎬 Faceless Video Engine Pro")
st.subheader("High-Pacing Hybrid Engine: 100% Free Web Photos + Free AI Art + Contextual Audio")

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
    with st.spinner("Fetching real web photos, generating script beats, mixing contextual BGM, and compiling video..."):
        video_file, transcript = build_video_pipeline(
            topic_input, context_input, niche_dropdown, aspect_ratio, uploaded_files, selected_mood
        )
        
        with col2:
            if video_file:
                st.success("High-Pacing Asset Rendered Successfully!")
                st.video(video_file)
                st.text_area("Generated Script Transcript", value=transcript, height=200, disabled=True)
            else:
                st.error(transcript)
