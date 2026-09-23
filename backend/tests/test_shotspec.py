import pytest
from pydantic import ValidationError

from app.shotspec import RenderModel, ShotSpec, lock_duration, to_4n1


@pytest.mark.parametrize("n,expected", [(0, 1), (1, 1), (2, 5), (5, 5), (6, 9), (88, 89), (89, 89), (90, 93)])
def test_to_4n1(n, expected):
    assert to_4n1(n) == expected


def test_plan_example_24fps_89_frames():
    # 計劃書範例：3.2 s 對白 + 0.25 + 0.25 @24fps = 88.8 → 89
    d = lock_duration(RenderModel.WAN22_5B, audio_s=3.2)
    assert (d.frames, d.fps, d.locked, d.mode) == (89, 24, True, "audio_locked")
    assert not d.needs_split


def test_a14b_uses_16fps_and_rounds_to_4n1():
    d = lock_duration(RenderModel.WAN22_A14B, audio_s=3.2)  # 3.7 s × 16 = 59.2 → 60 → 61
    assert (d.fps, d.frames) == (16, 61)


def test_long_dialogue_needs_split():
    d = lock_duration(RenderModel.WAN22_A14B, audio_s=6.0)
    assert d.frames > 81 and d.needs_split


def test_storyboard_mode_is_not_locked():
    d = lock_duration(RenderModel.WAN22_5B, target_s=2.0)
    assert (d.frames, d.locked, d.head_pad_s) == (49, False, 0)


def _spec(**kw):
    base = dict(shot_id="s1", track="A", duration=lock_duration(RenderModel.WAN22_A14B, target_s=2))
    return ShotSpec(**{**base, **kw})


def test_lip_sync_requires_dialogue():
    with pytest.raises(ValidationError, match="需要有對白"):
        _spec(lip_sync=True)


def test_duration_must_match_model():
    with pytest.raises(ValidationError, match="不符"):
        _spec(duration=lock_duration(RenderModel.WAN22_5B, target_s=2))


def test_post_order_is_fixed():
    with pytest.raises(ValidationError, match="後製順序"):
        _spec(post_order=("interp", "upscale", "lipsync", "ambience", "mix", "lut"))


def test_track_and_lipsync_are_orthogonal():
    from app.shotspec import Dialogue, VoiceProfile

    dlg = Dialogue(character_id="c", text="喝！", voice_profile=VoiceProfile(timbre_ref="x.wav"))
    assert _spec(track="A", lip_sync=True, dialogue=dlg).track == "A"
