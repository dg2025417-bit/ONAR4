import base64
import io
import json
import os
import tempfile

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

# ----------------------------------------------------------------------------
# 기본 설정
# ----------------------------------------------------------------------------
st.set_page_config(page_title="포토부스", page_icon="📸", layout="centered")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FRAME_PATH = os.path.join(APP_DIR, "assets", "frame.png")

# 업로드한 프레임 이미지에서 실측한 4개의 사진 슬롯 좌표 (x1, y1, x2, y2)
SLOTS = [
    (172, 84, 560, 373),
    (172, 421, 560, 710),
    (172, 759, 560, 1047),
    (172, 1096, 560, 1384),
]

NUM_SHOTS = len(SLOTS)

# ----------------------------------------------------------------------------
# 카메라 촬영용 커스텀 컴포넌트
#
# streamlit.components.v1.html()은 화면 표시만 될 뿐 JS -> Python 값 반환이
# 되지 않으므로, 반드시 declare_component(path=...)로 등록된 진짜 양방향
# 컴포넌트를 사용해야 한다. 프로젝트 폴더에 별도 파일을 추가하지 않기 위해,
# 아래 HTML을 앱 실행 시점에 임시 디렉터리에 자동으로 써 두고 그 경로를
# 컴포넌트로 등록한다 (저장소에는 파일이 추가되지 않음).
# ----------------------------------------------------------------------------
_CAMERA_COMPONENT_HTML = r"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<style>
  html, body {
    margin:0; padding:0;
    font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;
    background:transparent;
  }
  #app-root {
    display:flex;flex-direction:column;align-items:center;
    padding:4px 0 12px 0;
  }
  #video-wrap {
    position:relative;
    width:100%;
    max-width:520px;
    aspect-ratio:388/289;
    background:#000;
    border-radius:18px;
    overflow:hidden;
    box-shadow:0 6px 24px rgba(0,0,0,.35);
  }
  #video {
    width:100%;height:100%;object-fit:cover;
    transform:scaleX(-1); /* 거울 모드 */
  }
  #countdown {
    position:absolute;top:0;left:0;width:100%;height:100%;
    display:flex;align-items:center;justify-content:center;
    font-size:6rem;font-weight:800;color:#fff;
    text-shadow:0 0 20px rgba(0,0,0,.8);
    background:rgba(0,0,0,0.15);
    opacity:0;pointer-events:none;transition:opacity .1s;
  }
  #flash {
    position:absolute;top:0;left:0;width:100%;height:100%;
    background:#fff;opacity:0;pointer-events:none;
  }
  #status {
    margin-top:14px;font-size:1.1rem;font-weight:700;color:#222;
    text-align:center;
  }
  #shotBtn {
    margin-top:16px;width:100%;max-width:520px;height:56px;
    font-size:1.1rem;font-weight:800;color:#fff;border:none;border-radius:14px;
    background:linear-gradient(135deg,#2b2b2b,#111);cursor:pointer;
  }
  #shotBtn:disabled { opacity:.5;cursor:not-allowed; }
  #thumbs { display:flex;gap:8px;margin-top:14px; }
  #thumbs img {
    width:64px;height:48px;object-fit:cover;border-radius:6px;
    border:2px solid #333;
  }
</style>
</head>
<body>
  <div id="app-root">
    <div id="video-wrap">
      <video id="video" autoplay playsinline muted></video>
      <div id="countdown"></div>
      <div id="flash"></div>
    </div>

    <div id="status">카메라를 준비하고 있어요...</div>
    <button id="shotBtn" disabled>촬영 시작</button>
    <div id="thumbs"></div>

    <canvas id="canvas" style="display:none;"></canvas>
  </div>

<script>
  function sendMessageToStreamlit(type, data) {
    var outData = Object.assign({
      isStreamlitMessage: true,
      type: type,
    }, data);
    window.parent.postMessage(outData, "*");
  }

  function componentReady() {
    sendMessageToStreamlit("streamlit:componentReady", {apiVersion: 1});
  }

  function setFrameHeight(height) {
    sendMessageToStreamlit("streamlit:setFrameHeight", {height: height});
  }

  function setComponentValue(value) {
    sendMessageToStreamlit("streamlit:setComponentValue", {value: value, dataType: "json"});
  }

  function reportHeight() {
    setFrameHeight(document.documentElement.scrollHeight + 10);
  }

  let TOTAL_SHOTS = 4;
  let started = false;

  window.addEventListener("message", function(event) {
    const data = event.data;
    if (!data || data.type !== "streamlit:render") return;
    if (data.args && typeof data.args.num_shots === "number") {
      TOTAL_SHOTS = data.args.num_shots;
    }
    if (!started) {
      started = true;
      startCamera();
    }
    reportHeight();
  });

  componentReady();
  window.addEventListener("load", function() {
    setTimeout(reportHeight, 200);
  });

  const video = document.getElementById("video");
  const canvas = document.getElementById("canvas");
  const countdownEl = document.getElementById("countdown");
  const flashEl = document.getElementById("flash");
  const statusEl = document.getElementById("status");
  const shotBtn = document.getElementById("shotBtn");
  const thumbsEl = document.getElementById("thumbs");

  let shotsTaken = 0;
  let photos = [];
  let busy = false;

  async function startCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false
      });
      video.srcObject = stream;
      statusEl.textContent = "준비 완료! 버튼을 눌러 촬영을 시작하세요 (1/" + TOTAL_SHOTS + ")";
      shotBtn.disabled = false;
      shotBtn.textContent = "촬영 시작";
    } catch (err) {
      statusEl.textContent = "카메라 접근이 거부되었습니다. 브라우저 권한을 확인해주세요.";
      console.error(err);
    }
    setTimeout(reportHeight, 300);
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async function runCountdownAndCapture() {
    if (busy || shotsTaken >= TOTAL_SHOTS) return;
    busy = true;
    shotBtn.disabled = true;

    for (const n of [3, 2, 1]) {
      countdownEl.textContent = n;
      countdownEl.style.opacity = 1;
      await sleep(600);
      countdownEl.style.opacity = 0;
      await sleep(150);
    }

    flashEl.style.opacity = 1;
    setTimeout(() => { flashEl.style.opacity = 0; }, 150);

    const vw = video.videoWidth || 1280;
    const vh = video.videoHeight || 960;
    canvas.width = vw;
    canvas.height = vh;
    const ctx = canvas.getContext("2d");
    ctx.translate(vw, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, vw, vh);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.92);
    photos.push(dataUrl);
    shotsTaken += 1;

    const thumb = document.createElement("img");
    thumb.src = dataUrl;
    thumbsEl.appendChild(thumb);

    if (shotsTaken < TOTAL_SHOTS) {
      statusEl.textContent = shotsTaken + "/" + TOTAL_SHOTS + " 촬영 완료! 다음 촬영을 위해 버튼을 눌러주세요.";
      shotBtn.textContent = "다음 사진 촬영 (" + (shotsTaken + 1) + "/" + TOTAL_SHOTS + ")";
      shotBtn.disabled = false;
      busy = false;
    } else {
      statusEl.textContent = "촬영이 모두 끝났습니다! 결과를 준비하는 중...";
      shotBtn.style.display = "none";
      const tracks = video.srcObject ? video.srcObject.getTracks() : [];
      tracks.forEach(t => t.stop());
      setTimeout(function() {
        setComponentValue(photos);
      }, 400);
    }
    setTimeout(reportHeight, 200);
  }

  shotBtn.addEventListener("click", runCountdownAndCapture);
</script>
</body>
</html>
"""


@st.cache_resource
def _get_camera_component():
    tmp_dir = tempfile.mkdtemp(prefix="camera_component_")
    with open(os.path.join(tmp_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(_CAMERA_COMPONENT_HTML)
    return components.declare_component("camera_capture", path=tmp_dir)


_camera_component = _get_camera_component()


def camera_capture(num_shots=NUM_SHOTS, key=None):
    return _camera_component(num_shots=num_shots, key=key, default=None)


# 스트림릿 기본 여백/헤더 숨기기 + 배경 스타일
st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container {padding-top: 2rem; padding-bottom: 2rem;}
    .stButton>button {
        width: 100%;
        height: 3.2em;
        font-size: 1.15em;
        font-weight: 700;
        border-radius: 14px;
        background: linear-gradient(135deg,#2b2b2b,#111);
        color: #fff;
        border: none;
    }
    .stButton>button:hover {
        background: linear-gradient(135deg,#444,#000);
        color: #fff;
    }
    .center-text {text-align:center;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# 세션 상태 초기화
# ----------------------------------------------------------------------------
if "stage" not in st.session_state:
    st.session_state.stage = "start"   # start -> capture -> result
if "photos" not in st.session_state:
    st.session_state.photos = None     # 4장의 base64 dataURL 리스트
if "result_image" not in st.session_state:
    st.session_state.result_image = None


def go_to(stage):
    st.session_state.stage = stage
    st.rerun()


def reset_all():
    st.session_state.stage = "start"
    st.session_state.photos = None
    st.session_state.result_image = None
    st.rerun()


# ----------------------------------------------------------------------------
# 이미지 합성 로직
# ----------------------------------------------------------------------------
def crop_to_ratio(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """가운데를 기준으로 target 비율에 맞게 크롭 후 정확한 크기로 리사이즈"""
    target_ratio = target_w / target_h
    w, h = img.size
    ratio = w / h

    if ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    return img.resize((target_w, target_h), Image.LANCZOS)


def build_final_image(photo_datauris):
    frame = Image.open(FRAME_PATH).convert("RGB")
    result = frame.copy()

    for datauri, box in zip(photo_datauris, SLOTS):
        _, encoded = datauri.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        photo = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        cropped = crop_to_ratio(photo, w, h)
        result.paste(cropped, (x1, y1))

    return result


# ----------------------------------------------------------------------------
# 1. 시작 화면
# ----------------------------------------------------------------------------
def render_start():
    st.markdown("<h1 class='center-text'>📸 인생네컷 포토부스</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p class='center-text'>버튼을 누르고 카메라 앞에서 포즈를 취해보세요!<br>"
        "총 4번, 3-2-1 카운트다운 후 촬영됩니다.</p>",
        unsafe_allow_html=True,
    )
    st.write("")
    st.image(FRAME_PATH, use_container_width=True)
    st.write("")
    if st.button("🎬 시작하기"):
        go_to("capture")


# ----------------------------------------------------------------------------
# 2. 촬영 화면 (커스텀 HTML/JS 컴포넌트)
# ----------------------------------------------------------------------------
def render_capture():
    st.markdown("<h3 class='center-text'>촬영 중... (총 4컷)</h3>", unsafe_allow_html=True)

    component_value = camera_capture(num_shots=NUM_SHOTS, key="camera_capture")

    if component_value and st.session_state.photos is None:
        photos = component_value
        if isinstance(photos, str):
            try:
                photos = json.loads(photos)
            except (json.JSONDecodeError, TypeError):
                photos = None

        if isinstance(photos, list) and len(photos) == NUM_SHOTS:
            st.session_state.photos = photos
            st.session_state.result_image = build_final_image(photos)
            go_to("result")

    st.write("")
    if st.button("⬅ 처음으로"):
        reset_all()


# ----------------------------------------------------------------------------
# 3. 결과 화면
# ----------------------------------------------------------------------------
def render_result():
    st.markdown("<h1 class='center-text'>🎉 완성!</h1>", unsafe_allow_html=True)

    result_img = st.session_state.result_image
    if result_img is None and st.session_state.photos:
        result_img = build_final_image(st.session_state.photos)
        st.session_state.result_image = result_img

    if result_img is not None:
        st.image(result_img, use_container_width=True)

        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        buf.seek(0)

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "💾 저장하기 (PNG)",
                data=buf,
                file_name="photobooth.png",
                mime="image/png",
                use_container_width=True,
            )
        with col2:
            if st.button("🔄 다시하기", use_container_width=True):
                reset_all()
    else:
        st.warning("사진 데이터가 없습니다. 다시 시도해주세요.")
        if st.button("⬅ 처음으로"):
            reset_all()


# ----------------------------------------------------------------------------
# 라우팅
# ----------------------------------------------------------------------------
stage = st.session_state.stage
if stage == "start":
    render_start()
elif stage == "capture":
    render_capture()
elif stage == "result":
    render_result()
