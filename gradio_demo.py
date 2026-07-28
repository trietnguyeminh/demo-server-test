"""Optional Kaggle-friendly public demo using the same service layer."""
from __future__ import annotations

import json
from pathlib import Path

import gradio as gr
from PIL import Image, ImageDraw

from server.config import Settings
from server.engine import build_engine
from server.models import ModalityMode, SearchProfile, SearchRequest
from server.service import SearchService


settings = Settings.from_env()
service = SearchService(build_engine(settings), settings)


def preview_for(item_id: str, video_id: str, pts_time: float):
    path = service.engine.frame_path(item_id)
    if path is not None and Path(path).is_file():
        return str(path)
    image = Image.new("RGB", (640, 360), (17, 24, 39))
    draw = ImageDraw.Draw(image)
    draw.text((30, 145), f"{video_id} @ {pts_time:.2f}s", fill=(238, 242, 255))
    draw.text((30, 190), item_id, fill=(154, 168, 191))
    return image


def run_search(query: str, profile: str, ocr: str, asr: str):
    response = service.search(
        SearchRequest(
            query=query,
            profile=SearchProfile(profile),
            ocr=ModalityMode(ocr),
            asr=ModalityMode(asr),
            top_k=12,
        )
    )
    gallery = [
        (
            preview_for(hit.item_id, hit.video_id, hit.pts_time),
            f"#{hit.rank} {hit.video_id} @ {hit.pts_time:.2f}s | "
            f"{','.join(hit.modalities)}",
        )
        for hit in response.hits
    ]
    return gallery, json.dumps(response.model_dump(), ensure_ascii=False, indent=2)


with gr.Blocks(title="AIC Latency-first Demo") as demo:
    gr.Markdown("# AIC Latency-first Retrieval Demo")
    query = gr.Textbox(label="Query", value="người đánh trống Yamaha")
    with gr.Row():
        profile = gr.Dropdown(["fast", "auto", "accurate"], value="auto", label="Profile")
        ocr = gr.Dropdown(["off", "auto", "on"], value="auto", label="OCR")
        asr = gr.Dropdown(["off", "auto", "on"], value="auto", label="ASR")
    button = gr.Button("Search")
    gallery = gr.Gallery(label="Results", columns=4)
    raw = gr.Code(label="Response JSON", language="json")
    button.click(run_search, [query, profile, ocr, asr], [gallery, raw])


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", share=True)
