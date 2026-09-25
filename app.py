"""ブラウザで使うチャット UI（Streamlit）。

    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from localai import LocalAI, is_server_up, list_models, settings
from localai.config import FALLBACK_MODELS

st.set_page_config(page_title="ローカルAI チャット", page_icon="💬", layout="wide")

# ---- サーバー確認 -----------------------------------------------------------
if not is_server_up():
    st.error(
        "Ollama サーバーに接続できません。Ollama を起動してからページを再読み込みしてください。\n\n"
        "スタートメニューから **Ollama** を起動するか、ターミナルで `ollama serve` を実行します。"
    )
    st.stop()

installed = list_models()

# ---- サイドバー: 設定 -------------------------------------------------------
with st.sidebar:
    st.header("設定")
    if not installed:
        st.warning(
            "モデルが1つもありません。ターミナルで例えば\n\n"
            f"`ollama pull {settings.model}`\n\nを実行してください。"
        )
        st.stop()

    default_idx = installed.index(settings.model) if settings.model in installed else 0
    model = st.selectbox("モデル", installed, index=default_idx)
    system_prompt = st.text_area("システムプロンプト", settings.system_prompt, height=120)
    temperature = st.slider("temperature（創造性）", 0.0, 1.5, settings.temperature, 0.05)

    st.caption("画像を渡す（対応モデルのみ: qwen3.5 / gemma3:4b など）")
    uploaded = st.file_uploader("画像", type=["png", "jpg", "jpeg", "webp"])

    if st.button("会話をリセット", use_container_width=True):
        st.session_state.pop("ai", None)
        st.session_state.pop("messages", None)
        st.rerun()

    with st.expander("軽いモデルの候補"):
        st.code("\n".join(f"ollama pull {m}" for m in FALLBACK_MODELS))

# ---- セッション状態 ---------------------------------------------------------
if "ai" not in st.session_state:
    st.session_state.ai = LocalAI(model=model, system_prompt=system_prompt, temperature=temperature)
    st.session_state.messages = []

ai: LocalAI = st.session_state.ai
# サイドバー変更を反映（履歴は維持）
ai.model = model
ai.system_prompt = system_prompt
ai.options["temperature"] = temperature

# ---- 画面 -------------------------------------------------------------------
st.title("💬 ローカルAI チャット")
st.caption(f"モデル: `{model}` ・ すべてこのPC内で動作しています（外部送信なし）")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if prompt := st.chat_input("メッセージを入力"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    images = None
    if uploaded is not None:
        images = [uploaded.getvalue()]  # bytes をそのまま渡せる

    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(ai.ask_stream(prompt, images=images))
        except Exception as e:  # noqa: BLE001
            reply = f"エラー: {e}"
            st.error(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
