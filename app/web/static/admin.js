(function () {
  const sessionId = window.__CHAT_SESSION_ID__;
  const initialTemplates = Array.isArray(window.__MESSAGE_TEMPLATES__)
    ? window.__MESSAGE_TEMPLATES__
    : [];
  const chatBox = document.getElementById("chat-messages");
  const sendForm = document.getElementById("send-form");
  const messageInput = document.getElementById("message-input");
  const fileInput = document.getElementById("file-input");
  const closeBtn = document.getElementById("close-chat-btn");
  const templateList = document.getElementById("template-list");
  const templateForm = document.getElementById("template-form");
  const templateResetBtn = document.getElementById("template-reset-btn");
  const templateIdInput = document.getElementById("template-id");
  const templateTitleInput = document.getElementById("template-title");
  const templateTextInput = document.getElementById("template-text");

  let lastId = Number(chatBox?.dataset.lastId || 0);
  let templates = [...initialTemplates];

  if (!sessionId && !templateForm) {
    return;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatDate(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return iso;
    }
    return date.toLocaleString("ru-RU", {
      hour12: false,
    });
  }

  function renderAttachments(attachments) {
    if (!Array.isArray(attachments) || attachments.length === 0) {
      return "";
    }

    const items = attachments.map((att) => {
      if (att && att.url) {
        return `<li><a href="${escapeHtml(att.url)}" target="_blank" rel="noopener">Вложение</a></li>`;
      }

      if (att && att.filename) {
        const size = att.size ? ` (${escapeHtml(att.size)} байт)` : "";
        return `<li>${escapeHtml(att.filename)}${size}</li>`;
      }

      return "<li>Вложение</li>";
    });

    return `<ul class="attachment-list">${items.join("")}</ul>`;
  }

  function appendMessage(message) {
    if (!chatBox) {
      return;
    }

    const placeholder = chatBox.querySelector("p.muted");
    if (placeholder) {
      placeholder.remove();
    }

    const shouldAutoScroll = chatBox.scrollTop + chatBox.clientHeight + 120 >= chatBox.scrollHeight;
    const role = message.sender_role === "admin"
      ? "admin"
      : (message.sender_role === "bot" ? "bot" : "user");
    const label = role === "admin" ? "Админ" : (role === "bot" ? "Бот" : "Пользователь");

    const article = document.createElement("article");
    article.className = `message ${role === "admin" ? "message-admin" : (role === "bot" ? "message-bot" : "message-user")}`;
    article.dataset.messageId = String(message.id);
    article.innerHTML = `
      <div class="message-head">
        <span>${label}</span>
        <time datetime="${escapeHtml(message.created_at)}">${escapeHtml(formatDate(message.created_at))}</time>
      </div>
      <p>${escapeHtml(message.text || "")}</p>
      ${renderAttachments(message.attachment_data)}
    `;

    chatBox.appendChild(article);
    lastId = Math.max(lastId, Number(message.id) || 0);
    chatBox.dataset.lastId = String(lastId);

    if (shouldAutoScroll) {
      chatBox.scrollTop = chatBox.scrollHeight;
    }
  }

  async function loadMessages() {
    if (!sessionId) {
      return;
    }
    try {
      const response = await fetch(`/admin/api/chats/${sessionId}/messages?after_id=${lastId}`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const messages = payload.messages || [];
      messages.forEach(appendMessage);
    } catch (err) {
      console.error("poll error", err);
    }
  }

  function renderTemplateList() {
    if (!templateList) {
      return;
    }

    if (!templates.length) {
      templateList.innerHTML = '<p class="muted">Шаблоны пока не добавлены.</p>';
      return;
    }

    templateList.innerHTML = templates
      .map((tpl) => {
        const disabledAttr = sessionId ? "" : "disabled";
        return `
          <article class="template-item" data-template-id="${tpl.id}">
            <div class="template-item-head">
              <strong>${escapeHtml(tpl.title)}</strong>
              <div class="template-item-actions">
                <button type="button" class="secondary template-edit-btn" data-template-id="${tpl.id}">Редактировать</button>
                <button type="button" class="template-send-btn" data-template-id="${tpl.id}" ${disabledAttr}>Отправить</button>
              </div>
            </div>
            <p>${escapeHtml(tpl.text)}</p>
          </article>
        `;
      })
      .join("");
  }

  function resetTemplateForm() {
    if (!templateForm) {
      return;
    }
    templateIdInput.value = "";
    templateTitleInput.value = "";
    templateTextInput.value = "";
    templateTitleInput.focus();
  }

  async function upsertTemplate(event) {
    event.preventDefault();
    if (!templateTitleInput || !templateTextInput) {
      return;
    }
    const title = templateTitleInput.value.trim();
    const text = templateTextInput.value.trim();
    if (!title || !text) {
      alert("Заполните название и текст шаблона");
      return;
    }

    const templateId = templateIdInput.value.trim();
    const method = templateId ? "PUT" : "POST";
    const url = templateId
      ? `/admin/api/templates/${templateId}`
      : "/admin/api/templates";

    const response = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title, text }),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(payload.detail || "Не удалось сохранить шаблон");
      return;
    }

    const saved = payload.template;
    const existingIdx = templates.findIndex((tpl) => String(tpl.id) === String(saved.id));
    if (existingIdx >= 0) {
      templates[existingIdx] = saved;
    } else {
      templates.unshift(saved);
    }

    templates.sort((a, b) => Number(b.id) - Number(a.id));
    renderTemplateList();
    resetTemplateForm();
  }

  async function sendTemplate(templateId) {
    if (!sessionId) {
      alert("Сначала выберите чат");
      return;
    }

    const response = await fetch(
      `/admin/api/chats/${sessionId}/templates/${templateId}/send`,
      { method: "POST" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(payload.detail || "Не удалось отправить шаблон");
      return;
    }

    if (payload.message) {
      appendMessage(payload.message);
    }
  }

  renderTemplateList();

  if (chatBox) {
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  if (sessionId) {
    setInterval(loadMessages, 3000);
  }

  if (templateForm) {
    templateForm.addEventListener("submit", async (event) => {
      try {
        await upsertTemplate(event);
      } catch (err) {
        alert("Ошибка сохранения шаблона");
      }
    });
  }

  if (templateResetBtn) {
    templateResetBtn.addEventListener("click", () => {
      resetTemplateForm();
    });
  }

  if (templateList) {
    templateList.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const templateId = target.dataset.templateId;
      if (!templateId) {
        return;
      }

      const template = templates.find((item) => String(item.id) === String(templateId));
      if (!template) {
        return;
      }

      if (target.classList.contains("template-edit-btn")) {
        templateIdInput.value = String(template.id);
        templateTitleInput.value = template.title;
        templateTextInput.value = template.text;
        templateTitleInput.focus();
        return;
      }

      if (target.classList.contains("template-send-btn")) {
        try {
          await sendTemplate(template.id);
        } catch (err) {
          alert("Ошибка отправки шаблона");
        }
      }
    });
  }

  if (sendForm && messageInput) {
    sendForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = messageInput.value.trim();
      const file = fileInput && fileInput.files ? fileInput.files[0] : null;
      if (!text && !file) {
        return;
      }

      messageInput.disabled = true;
      if (fileInput) {
        fileInput.disabled = true;
      }

      try {
        const formData = new FormData();
        formData.append("text", text);
        if (file) {
          formData.append("file", file);
        }

        const response = await fetch(`/admin/api/chats/${sessionId}/messages`, {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          const detail = payload.detail || "Ошибка отправки";
          alert(detail);
          return;
        }

        const payload = await response.json();
        appendMessage(payload.message);
        messageInput.value = "";
        if (fileInput) {
          fileInput.value = "";
        }
      } catch (err) {
        alert("Не удалось отправить сообщение");
      } finally {
        messageInput.disabled = false;
        if (fileInput) {
          fileInput.disabled = false;
        }
        messageInput.focus();
      }
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", async () => {
      if (!confirm("Закрыть этот чат?")) {
        return;
      }

      try {
        const response = await fetch(`/admin/api/chats/${sessionId}/close`, {
          method: "POST",
        });
        if (!response.ok) {
          alert("Не удалось закрыть чат");
          return;
        }
        window.location.reload();
      } catch (err) {
        alert("Ошибка сети при закрытии чата");
      }
    });
  }
})();
