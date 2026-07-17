import os
import json
import time
import asyncio
import requests
import streamlit as st
from huggingface_hub import InferenceClient
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

# Safely extract the secret token directly from the Streamlit Cloud Key Vault
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

async def generate_audio(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def build_video_pipeline(topic, detailed_context, niche):
    config = NICHE_CONFIGS[niche]
    
    # 1. SCRIPT PRODUCTION ENGINE
    if detailed_context and detailed_context.strip():
        prompt = f"""
        Adapt and condense the following source context into a short 3-scene video script:
        {detailed_context}
        
        You MUST respond ONLY with a raw valid JSON object matching this exact structure:
        {{
          "full_narration": "The entire script text written seamlessly here without scene indicators.",
          "scenes": [
            {{"scene_number": 1, "image_prompt": "Detailed description of a visual matching the first section"}},
            {{"scene_number": 2, "image_prompt": "Detailed description of a visual matching the middle section"}},
            {{"scene_number": 3, "image_prompt": "Detailed description of a visual matching the final section"}}
          ]
        }}
        """
    else:
        prompt = f"""
        Write a short 3-scene video script about: {topic}.
        You MUST respond ONLY with a raw valid JSON object matching this exact structure:
        {{
          "full_narration": "The entire script text written seamlessly here without scene indicators.",
          "scenes": [
            {{"scene_number": 1, "image_prompt": "Detailed visual description for scene 1"}},
            {{"scene_number": 2, "image_prompt": "Detailed visual description for scene 2"}},
            {{"scene_number": 3, "image_prompt": "Detailed visual description for scene 3"}}
          ]
        }}
        """

    try:
        completion = client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[
                {"role": "system", "content": config["system_prompt"] + " Return strictly raw valid JSON. Do not include markdown codeblocks."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1200
        )
        raw_text = completion.choices[0].message.content.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text.removeprefix("```json").removesuffix("```").strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.removeprefix("```").removesuffix("```").strip()
            
        data = json.loads(raw_text)
    except Exception as e:
        return None, f"Text Generation Error: {str(e)}"

    # 2. AUDIO GENERATION
    try:
        audio_path = "narration.mp3"
        asyncio.run(generate_audio(data["full_narration"], config["voice"], audio_path))
        
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration
        scene_duration = total_duration / len(data["scenes"])
    except Exception as e:
        return None, f"Audio Generation Error: {str(e)}"

    # 3. OPEN-SOURCE VISUAL GENERATION VIA FLUX
    image_clips = []
    try:
        for i, scene in enumerate(data["scenes"]):
            final_prompt = scene["image_prompt"] + config["style_suffix"]
            image = client.text_to_image(prompt=final_prompt, model="black-forest-labs/FLUX.1-schnell")
            
            img_path = f"scene_{i}.jpg"
            image.save(img_path)
            
            img_clip = ImageClip(img_path).set_duration(scene_duration)
            image_clips.append(img_clip)
    except Exception as e:
        return None, f"Image Generation Error: {str(e)}"

    # 4. STITCH AND CLIP RENDER
    try:
        video = concatenate_videoclips(image_clips, method="compose")
        video = video.set_audio(audio_clip)
        
        output_video_path = "final_output.mp4"
        video.write_videofile(output_video_path, fps=12, codec="libx264", audio_codec="aac")
        return output_video_path, data["full_narration"]
    except Exception as e:
        return None, f"Compilation Render Error: {str(e)}"

# Frontend Application Layout Layout
st.set_page_config(page_title="Faceless Video Engine", layout="wide")
st.title("🎬 Free Automated Video Engine")
st.subheader("100% Free Cloud Compute Pipeline (Powered by Streamlit)")

col1, col2 = st.columns(2)

with col1:
    topic_input = st.text_input("Option A: Short Topic Idea", placeholder="e.g., The strategic importance of the Malacca Strait")
    context_input = st.text_area("Option B: Paste Detailed Context (Overrides Option A)", placeholder="Paste your detailed transcripts or marketing reports here...", height=250)
    niche_dropdown = st.selectbox("Select Target Niche", ["Marketing", "Geopolitics"])
    submit_btn = st.button("Build Video Asset", type="primary")

if submit_btn:
    with st.spinner("Processing cloud text engines, generating audio tracking, and exporting media elements..."):
        video_file, transcript = build_video_pipeline(topic_input, context_input, niche_dropdown)
        
        with col2:
            if video_file:
                st.success("Video Asset Rendered Successfully!")
                st.video(video_file)
                st.text_area("Generated Script Transcript", value=transcript, height=200, disabled=True)
            else:
                st.error(transcript)
