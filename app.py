import os
import json
import asyncio
import requests
import streamlit as st
import json_repair
from PIL import Image
from huggingface_hub import InferenceClient
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

# Extract HF token from Streamlit Secrets
HF_TOKEN = st.secrets["HF_TOKEN"]
client = InferenceClient(token=HF_TOKEN)

NICHE_CONFIGS = {
    "Geopolitics": {
        "voice": "en-GB-RyanNeural",
        "style_suffix": ", gritty cinematic documentary style, satellite map aesthetic, dark atmospheric lighting, 8k, photorealistic",
        "system_prompt": "You are a top geopolitical analyst. Write a high-tension, fact-grounded documentary narrative."
    },
    "Marketing": {
        "voice": "en-US-BrianNeural",
        "style_suffix": ", modern high-contrast corporate vector illustration, sleek technology startup aesthetic, clean minimalist design",
        "system_prompt": "You are an elite growth marketer. Write a fast-paced, highly engaging business case study breakdown."
    }
}

ASPECT_RATIOS = {
    "16:9 (Landscape - YouTube)": (1280, 720),
    "9:16 (Vertical - Shorts / Reels)": (720, 1280),
    "1:1 (Square)": (800, 800)
}

def prepare_image_aspect(image_path, target_w, target_h):
    """Crop and resize any image (AI or uploaded) to fit the target aspect ratio perfectly."""
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

def apply_ken_burns_motion(clip, target_w, target_h):
    """Adds a smooth, continuous slow zoom-in motion to static images."""
    def zoom_fn(t):
        return 1 + 0.08 * (t / max(clip.duration, 1))

    zoomed = clip.resize(zoom_fn)
    return zoomed.crop(x_center=zoomed.w / 2, y_center=zoomed.h / 2, width=target_w, height=target_h)

async def generate_audio(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def build_video_pipeline(topic, detailed_context, niche, aspect_ratio_label, uploaded_files):
    config = NICHE_CONFIGS[niche]
    target_w, target_h = ASPECT_RATIOS[aspect_ratio_label]
    
    # Process custom uploaded files if present
    custom_image_paths = []
    if uploaded_files:
        for idx, file in enumerate(uploaded_files):
            custom_path = f"custom_{idx}.jpg"
            with open(custom_path, "wb") as f:
                f.write(file.getbuffer())
            prepare_image_aspect(custom_path, target_w, target_h)
            custom_image_paths.append(custom_path)

    # 1. GENERATE DYNAMIC SCRIPT WITH 8-12 SCENES
    if detailed_context and detailed_context.strip():
        prompt = f"""
        Adapt and condense the following source text into a highly engaging YouTube video script.
        SOURCE TEXT:
        {detailed_context}
        
        CRITICAL: Break the narration down into 8 to 12 distinct sequential visual scenes to ensure high visual pacing throughout the video.
        
        Respond ONLY with a raw JSON object matching this exact structure:
        {{
          "full_narration": "The entire script text written seamlessly here without scene labels.",
          "scenes": [
            {{"scene_number": 1, "image_prompt": "Detailed description of a visual matching scene 1"}},
            ...
            {{"scene_number": 8, "image_prompt": "Detailed description of a visual matching scene 8"}}
          ]
        }}
        """
    else:
        prompt = f"""
        Write an engaging video script about: {topic}.
        CRITICAL: Break the video into 8 to 12 distinct sequential visual scenes for fast-paced visual storytelling.
        
        Respond ONLY with a raw JSON object matching this exact structure:
        {{
          "full_narration": "The entire script text written seamlessly here without scene labels.",
          "scenes": [
            {{"scene_number": 1, "image_prompt": "Detailed visual description for scene 1"}},
            ...
            {{"scene_number": 8, "image_prompt": "Detailed visual description for scene 8"}}
          ]
        }}
        """

    try:
        completion = client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[
                {"role": "system", "content": config["system_prompt"] + " Return strictly raw JSON. Ensure 8-12 scene breaks."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=3000
        )
        raw_text = completion.choices[0].message.content.strip()
        data = json_repair.loads(raw_text)
        
        if not isinstance(data, dict) or "full_narration" not in data or "scenes" not in data:
            raise ValueError("Parsed output missing required keys: 'full_narration' or 'scenes'.")
            
    except Exception as e:
        return None, f"Script Generation Error: {str(e)}\nRaw Response: {raw_text if 'raw_text' in locals() else 'None'}"

    # 2. AUDIO TIMELINE GENERATION
    try:
        audio_path = "narration.mp3"
        asyncio.run(generate_audio(data["full_narration"], config["voice"], audio_path))
        
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration
        scene_duration = total_duration / len(data["scenes"])
    except Exception as e:
        return None, f"Audio Generation Error: {str(e)}"

    # 3. VISUAL ENGINE (FLUX GENERATION + CUSTOM UPLOADS HYBRID)
    image_clips = []
    try:
        for i, scene in enumerate(data["scenes"]):
            img_path = f"scene_{i}.jpg"
            
            # If user provided custom images, intersperse them into scenes
            if custom_image_paths and (i % 2 == 0 or i >= len(data["scenes"]) - len(custom_image_paths)):
                custom_selected = custom_image_paths[i % len(custom_image_paths)]
                img = Image.open(custom_selected)
                img.save(img_path)
            else:
                # Generate via FLUX
                final_prompt = scene["image_prompt"] + config["style_suffix"]
                image = client.text_to_image(prompt=final_prompt, model="black-forest-labs/FLUX.1-schnell")
                image.save(img_path)
                prepare_image_aspect(img_path, target_w, target_h)
            
            # Build clip with dynamic zoom motion
            raw_clip = ImageClip(img_path).set_duration(scene_duration)
            motion_clip = apply_ken_burns_motion(raw_clip, target_w, target_h)
            image_clips.append(motion_clip)
            
    except Exception as e:
        return None, f"Visual Rendering Error: {str(e)}"

    # 4. COMPILATION AND EXPORT
    try:
        video = concatenate_videoclips(image_clips, method="compose")
        video = video.set_audio(audio_clip)
        
        output_video_path = "final_output.mp4"
        video.write_videofile(output_video_path, fps=15, codec="libx264", audio_codec="aac")
        return output_video_path, data["full_narration"]
    except Exception as e:
        return None, f"Video Compilation Error: {str(e)}"

# STREAMLIT UI DASHBOARD
st.set_page_config(page_title="Faceless Video Engine", layout="wide")
st.title("🎬 Faceless Video Engine Pro")
st.subheader("Dynamic Multi-Scene Engine with Ken Burns Motion & Custom Uploads")

col1, col2 = st.columns([1, 1])

with col1:
    topic_input = st.text_input("Option A: Short Topic Idea", placeholder="e.g., LEGO's scarcity marketing strategy")
    context_input = st.text_area("Option B: Paste Detailed Context (Overrides Option A)", placeholder="Paste transcripts, articles, or case studies...", height=200)
    
    st.markdown("---")
    niche_dropdown = st.selectbox("Select Target Niche", ["Marketing", "Geopolitics"])
    aspect_ratio = st.selectbox("Select Aspect Ratio", list(ASPECT_RATIOS.keys()), index=0)
    
    uploaded_files = st.file_uploader(
        "Upload Custom Images/Graphics (Optional - Will intersperse with AI visuals)", 
        type=["png", "jpg", "jpeg"], 
        accept_multiple_files=True
    )
    
    submit_btn = st.button("Build Video Asset", type="primary")

if submit_btn:
    with st.spinner("Writing script, generating audio, processing visual motion, and rendering video..."):
        video_file, transcript = build_video_pipeline(
            topic_input, context_input, niche_dropdown, aspect_ratio, uploaded_files
        )
        
        with col2:
            if video_file:
                st.success("Video Asset Rendered Successfully!")
                st.video(video_file)
                st.text_area("Generated Script Transcript", value=transcript, height=200, disabled=True)
            else:
                st.error(transcript)
