const BACKEND_URL = "http://127.0.0.1:8765";

const statusEl = document.getElementById("backend-status");
const conversationEl = document.getElementById("conversation");
const form = document.getElementById("ask-form");
const input = document.getElementById("query-input");
const askBtn = document.getElementById("ask-btn");

async function checkHealth() {
  try {
    const res = await fetch(`${BACKEND_URL}/health`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    statusEl.textContent = "Backend: connected";
    statusEl.className = "ok";
  } catch (err) {
    statusEl.textContent = `Backend unreachable: ${err.message}`;
    statusEl.className = "err";
  }
}

function appendMessage(text, className) {
  const div = document.createElement("div");
  div.className = `msg ${className}`;
  div.textContent = text;
  conversationEl.appendChild(div);
  conversationEl.scrollTop = conversationEl.scrollHeight;
  return div;
}

function appendSources(sources) {
  if (!sources || sources.length === 0) return;
  const wrap = document.createElement("div");
  wrap.className = "sources";
  const label = document.createElement("div");
  label.textContent = "Sources:";
  wrap.appendChild(label);
  const ul = document.createElement("ul");
  for (const s of sources) {
    const li = document.createElement("li");
    li.textContent = `${s.note_title} (${s.folder})`;
    ul.appendChild(li);
  }
  wrap.appendChild(ul);
  conversationEl.appendChild(wrap);
  conversationEl.scrollTop = conversationEl.scrollHeight;
}

async function askQuestion(query) {
  askBtn.disabled = true;
  askBtn.textContent = "Asking...";

  appendMessage(query, "question");

  try {
    const res = await fetch(`${BACKEND_URL}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, k: 5 }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    appendMessage(data.answer, "answer");
    appendSources(data.sources);
  } catch (err) {
    appendMessage(`Error: ${err.message}`, "error");
  } finally {
    askBtn.disabled = false;
    askBtn.textContent = "Ask";
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const query = input.value.trim();
  if (!query) return;
  input.value = "";
  askQuestion(query);
});

// ---------------------------------------------------------------------------
// Voice input (5d) + dictation (5e-ii) — share the same MediaRecorder
// pipeline, branching on `recordingMode` for what happens when it stops.
// ---------------------------------------------------------------------------

const recordBtn = document.getElementById("record-btn");
let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;
let recordingMode = "voice-ask"; // or "dictation"

async function startRecording(mode = "voice-ask") {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    if (mode === "voice-ask") {
      appendMessage(`Microphone access failed: ${err.message}`, "error");
    } else {
      console.error("Dictation mic access failed:", err);
    }
    return;
  }

  recordingMode = mode;
  recordedChunks = [];
  mediaRecorder = new MediaRecorder(stream);

  mediaRecorder.addEventListener("dataavailable", (e) => {
    if (e.data.size > 0) recordedChunks.push(e.data);
  });

  mediaRecorder.addEventListener("stop", () => {
    // Stop the mic stream itself, not just the recorder, so the OS mic
    // indicator/light turns off between recordings.
    stream.getTracks().forEach((track) => track.stop());
    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType });
    if (recordingMode === "dictation") {
      sendDictation(blob);
    } else {
      sendVoiceAsk(blob);
    }
  });

  mediaRecorder.start();
  isRecording = true;

  if (mode === "voice-ask") {
    recordBtn.textContent = "⏹ Stop";
    recordBtn.classList.add("recording");
  }
}

function stopRecording() {
  if (mediaRecorder && isRecording) {
    mediaRecorder.stop();
    isRecording = false;
    recordBtn.textContent = "🎙 Record";
    recordBtn.classList.remove("recording");
  }
}

recordBtn.addEventListener("click", () => {
  if (isRecording) {
    stopRecording();
  } else {
    startRecording("voice-ask");
  }
});

function playAnswerAudio(audioBase64, mimeType) {
  if (!audioBase64) return;
  const audio = new Audio(`data:${mimeType};base64,${audioBase64}`);
  audio.play().catch((err) => {
    console.warn("Playback failed:", err);
  });
}

async function sendVoiceAsk(blob) {
  recordBtn.disabled = true;
  askBtn.disabled = true;
  appendMessage("(transcribing voice question...)", "answer");

  const formData = new FormData();
  const extension = blob.type.includes("webm") ? "webm" : "wav";
  formData.append("audio", blob, `recording.${extension}`);
  formData.append("k", "5");
  formData.append("speak", "true");

  try {
    const res = await fetch(`${BACKEND_URL}/voice-ask`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    conversationEl.lastChild.remove();
    appendMessage(data.transcript || "(no speech detected)", "question");
    appendMessage(data.answer_text, "answer");
    appendSources(data.sources);
    playAnswerAudio(data.audio_base64, data.mime_type);
  } catch (err) {
    conversationEl.lastChild.remove();
    appendMessage(`Error: ${err.message}`, "error");
  } finally {
    recordBtn.disabled = false;
    askBtn.disabled = false;
  }
}

// 5e-ii: dictation into whatever field currently has OS focus (not our own
// window). Alt+Space press/release is handled in Rust, which shows/hides
// the indicator window and emits these two events; the actual mic capture
// stays in JS, reusing the exact MediaRecorder pipeline already proven in
// 5d, rather than adding a separate native audio-capture crate.
async function sendDictation(blob) {
  const formData = new FormData();
  const extension = blob.type.includes("webm") ? "webm" : "wav";
  formData.append("audio", blob, `dictation.${extension}`);

  try {
    const res = await fetch(`${BACKEND_URL}/transcribe`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    if (data.text && data.text.trim()) {
      await window.__TAURI__.core.invoke("inject_text", { text: data.text });
    }
  } catch (err) {
    // No good place to surface an error visually at this point (the user's
    // focus is long gone from our window) — log it for now. If dictation
    // errors turn out to be common in practice, worth revisiting how to
    // surface this (e.g. briefly changing the indicator window's text
    // before it hides).
    console.error("Dictation failed:", err);
  }
}

if (window.__TAURI__ && window.__TAURI__.event) {
  window.__TAURI__.event.listen("start-dictation", () => {
    if (!isRecording) startRecording("dictation");
  });
  window.__TAURI__.event.listen("stop-dictation", () => {
    if (isRecording && recordingMode === "dictation") stopRecording();
  });
}

checkHealth();
