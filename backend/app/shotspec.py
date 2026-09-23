"""ShotSpec：每個鏡頭的決定性規格（UI.md §10.1）。

幀數規則依 docs/可行性評估.md C-2 修正：Wan 的幀數必須是 4n+1，且有單次生成上限。
"""

import math
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class RenderModel(StrEnum):
    WAN22_A14B = "wan2.2_i2v_A14B_fp8"
    WAN22_5B = "wan2.2_ti2v_5B"


# 原生 fps 與單次生成最大幀數
MODEL_LIMITS: dict[RenderModel, tuple[int, int]] = {
    RenderModel.WAN22_A14B: (16, 81),
    RenderModel.WAN22_5B: (24, 121),
}

DEFAULT_PAD_S = 0.25

# 後製順序固定：口型必須在低解析 / 原生 fps 時做（可行性評估 C-5）
POST_ORDER = ("lipsync", "interp", "upscale", "ambience", "mix", "lut")


def to_4n1(frames: int) -> int:
    """無條件進位到下一個 4n+1（1, 5, 9, …）。"""
    if frames <= 1:
        return 1
    return math.ceil((frames - 1) / 4) * 4 + 1


class Duration(BaseModel):
    mode: str = Field("storyboard", pattern="^(audio_locked|storyboard|manual)$")
    audio_duration_s: float | None = None
    head_pad_s: float = DEFAULT_PAD_S
    tail_pad_s: float = DEFAULT_PAD_S
    total_s: float
    fps: int
    frames: int
    max_frames: int
    locked: bool = False

    @property
    def needs_split(self) -> bool:
        return self.frames > self.max_frames

    @model_validator(mode="after")
    def _check_frames(self):
        if self.frames != to_4n1(self.frames):
            raise ValueError(f"frames={self.frames} 不是 4n+1")
        return self


def lock_duration(
    model: RenderModel,
    *,
    audio_s: float | None = None,
    target_s: float | None = None,
    head_pad_s: float = DEFAULT_PAD_S,
    tail_pad_s: float = DEFAULT_PAD_S,
) -> Duration:
    """由對白時長（優先）或分鏡目標秒數算出鎖定的幀數。"""
    fps, max_frames = MODEL_LIMITS[model]
    if audio_s is not None:
        seconds, mode = audio_s + head_pad_s + tail_pad_s, "audio_locked"
    elif target_s is not None:
        seconds, mode = target_s, "storyboard"
    else:
        raise ValueError("需要 audio_s 或 target_s")
    frames = to_4n1(math.ceil(round(seconds * fps, 6)))
    return Duration(
        mode=mode,
        audio_duration_s=audio_s,
        head_pad_s=head_pad_s if audio_s is not None else 0,
        tail_pad_s=tail_pad_s if audio_s is not None else 0,
        total_s=round(frames / fps, 4),
        fps=fps,
        frames=frames,
        max_frames=max_frames,
        locked=audio_s is not None,
    )


class VoiceProfile(BaseModel):
    timbre_ref: str
    style_category: str | None = None
    emotion: str | None = None
    instruct_prompt: str | None = None
    pace: float = Field(1.0, ge=0.5, le=2.0)
    pitch_shift: float = 0.0
    secondary_style_ref: str | None = None


class AudioQC(BaseModel):
    spk_sim_min: float = 0.85
    av_sync_max_ms: int = 40


class Dialogue(BaseModel):
    enabled: bool = True
    character_id: str
    text: str
    voice_profile: VoiceProfile
    audio_qc: AudioQC = AudioQC()


class Render(BaseModel):
    model: RenderModel = RenderModel.WAN22_A14B
    strategy: str = "V2a_refine"
    draft_steps: int = 6
    final_steps: int = 6


class ShotSpec(BaseModel):
    shot_id: str
    scene: str | None = None
    description: str = ""
    # track（視覺生成方式）與 lip_sync（口型）是正交欄位
    track: str = Field(pattern="^[AB]$")
    lip_sync: bool = False
    duration: Duration
    dialogue: Dialogue | None = None
    render: Render = Render()
    post_order: tuple[str, ...] = POST_ORDER
    candidates: int = Field(2, ge=1, le=8)
    status: str = "DRAFT"

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.lip_sync and not (self.dialogue and self.dialogue.enabled):
            raise ValueError("lip_sync=true 需要有對白")
        fps, max_frames = MODEL_LIMITS[self.render.model]
        if (self.duration.fps, self.duration.max_frames) != (fps, max_frames):
            raise ValueError(f"duration 的 fps/max_frames 與模型 {self.render.model} 不符")
        if tuple(self.post_order) != POST_ORDER:
            raise ValueError(f"後製順序必須是 {POST_ORDER}")
        return self
