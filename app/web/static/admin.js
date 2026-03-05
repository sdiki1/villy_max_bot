(function () {
  const MOSCOW_TIME_ZONE = "Europe/Moscow";
  const sessionId = window.__CHAT_SESSION_ID__;
  const initialTemplates = Array.isArray(window.__MESSAGE_TEMPLATES__)
    ? window.__MESSAGE_TEMPLATES__
    : [];
  const initialWbAutoReply = window.__WB_AUTO_REPLY__ && typeof window.__WB_AUTO_REPLY__ === "object"
    ? window.__WB_AUTO_REPLY__
    : {
      is_enabled: false,
      answer_template: "",
      feedback_ai_enabled: false,
      feedback_ai_prompt: "",
      updated_at: "",
    };
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
  const wbAutoReplyForm = document.getElementById("wb-auto-reply-form");
  const wbAutoReplyEnabledInput = document.getElementById("wb-auto-reply-enabled");
  const wbAutoReplyTemplateInput = document.getElementById("wb-auto-reply-template");
  const wbAutoReplyStatus = document.getElementById("wb-auto-reply-status");
  const wbFeedbackAiForm = document.getElementById("wb-feedback-ai-form");
  const wbFeedbackAiEnabledInput = document.getElementById("wb-feedback-ai-enabled");
  const wbFeedbackAiPromptInput = document.getElementById("wb-feedback-ai-prompt");
  const wbFeedbackAiStatus = document.getElementById("wb-feedback-ai-status");
  const renameUserForm = document.getElementById("rename-user-form");
  const renameUserInput = document.getElementById("rename-user-input");
  const selectedUserName = document.getElementById("selected-user-name");
  const archiveUserBtn = document.getElementById("archive-user-btn");

  let lastId = Number(chatBox?.dataset.lastId || 0);
  let templates = [...initialTemplates];

  if (!sessionId && !templateForm && !wbAutoReplyForm && !wbFeedbackAiForm) {
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
    const formatted = date.toLocaleString("ru-RU", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: MOSCOW_TIME_ZONE,
    });
    return `${formatted} МСК`;
  }

  function refreshRenderedTimes() {
    const nodes = document.querySelectorAll("time[datetime]");
    nodes.forEach((node) => {
      const iso = node.getAttribute("datetime");
      if (!iso) {
        return;
      }
      node.textContent = formatDate(iso);
    });
  }

  function isImageAttachment(att) {
    if (!att || !att.url) {
      return false;
    }
    const type = String(att.type || "").toLowerCase();
    const contentType = String(att.content_type || "").toLowerCase();
    const filename = String(att.filename || "").toLowerCase();
    if (type === "image" || contentType.startsWith("image/")) {
      return true;
    }
    return [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"]
      .some((ext) => filename.endsWith(ext));
  }

  function renderAttachments(attachments) {
    if (!Array.isArray(attachments) || attachments.length === 0) {
      return "";
    }

    const items = attachments.map((att) => {
      if (isImageAttachment(att)) {
        const name = att.filename ? `<div class="attachment-meta">${escapeHtml(att.filename)}</div>` : "";
        return `
          <li class="attachment-item">
            <a href="${escapeHtml(att.url)}" class="attachment-image-link" target="_blank" rel="noopener">
              <img src="${escapeHtml(att.url)}" alt="${escapeHtml(att.filename || "Изображение")}" class="attachment-image" loading="lazy" />
            </a>
            ${name}
          </li>
        `;
      }

      if (att && att.url) {
        return `<li class="attachment-item"><a href="${escapeHtml(att.url)}" target="_blank" rel="noopener">Открыть вложение</a></li>`;
      }

      if (att && att.filename) {
        const size = att.size ? ` (${escapeHtml(att.size)} байт)` : "";
        return `<li class="attachment-item">${escapeHtml(att.filename)}${size}</li>`;
      }

      return '<li class="attachment-item">Вложение</li>';
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
    article.dataset.maxMessageId = String(message.max_message_id || "");
    article.innerHTML = `
      <div class="message-head">
        <span>${label}</span>
        <div class="message-head-actions">
          <time datetime="${escapeHtml(message.created_at)}">${escapeHtml(formatDate(message.created_at))}</time>
          <button
            type="button"
            class="secondary message-delete-btn"
            data-message-id="${escapeHtml(message.id)}"
            ${message.max_message_id ? "" : "disabled"}
          >
            Удалить у всех
          </button>
        </div>
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

  async function deleteMessageForAll(messageId) {
    const response = await fetch(`/admin/api/messages/${messageId}`, {
      method: "DELETE",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Не удалось удалить сообщение");
    }
  }

  async function renameUser(event) {
    event.preventDefault();
    if (!sessionId || !renameUserInput) {
      return;
    }

    const displayName = renameUserInput.value.trim();
    const response = await fetch(`/admin/api/chats/${sessionId}/user`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ display_name: displayName }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(payload.detail || "Не удалось переименовать пользователя");
      return;
    }
    if (selectedUserName && payload.user && payload.user.display_name) {
      selectedUserName.textContent = String(payload.user.display_name);
    }
    window.location.reload();
  }

  async function toggleArchiveUser() {
    if (!sessionId || !archiveUserBtn) {
      return;
    }
    const currentArchived = archiveUserBtn.dataset.archived === "true";
    const nextArchived = !currentArchived;
    const response = await fetch(`/admin/api/chats/${sessionId}/archive`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ is_archived: nextArchived }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(payload.detail || "Не удалось изменить архивный статус");
      return;
    }
    window.location.reload();
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

  function updateWbStatus(settings) {
    if (!wbAutoReplyStatus) {
      return;
    }

    const enabled = Boolean(settings && settings.is_enabled);
    const updatedAt = settings && settings.updated_at
      ? formatDate(settings.updated_at)
      : "";

    wbAutoReplyStatus.textContent = enabled
      ? `Автоответы включены${updatedAt ? ` · обновлено ${updatedAt}` : ""}`
      : `Автоответы выключены${updatedAt ? ` · обновлено ${updatedAt}` : ""}`;
  }

  function updateWbFeedbackAiStatus(settings) {
    if (!wbFeedbackAiStatus) {
      return;
    }

    const enabled = Boolean(settings && settings.feedback_ai_enabled);
    const updatedAt = settings && settings.updated_at
      ? formatDate(settings.updated_at)
      : "";

    wbFeedbackAiStatus.textContent = enabled
      ? `AI-ответы включены${updatedAt ? ` · обновлено ${updatedAt}` : ""}`
      : `AI-ответы выключены${updatedAt ? ` · обновлено ${updatedAt}` : ""}`;
  }

  function hydrateWbForm(settings) {
    if (!wbAutoReplyEnabledInput || !wbAutoReplyTemplateInput) {
      return;
    }
    wbAutoReplyEnabledInput.checked = Boolean(settings && settings.is_enabled);
    wbAutoReplyTemplateInput.value = String(settings?.answer_template || "");
    updateWbStatus(settings || {});

    if (wbFeedbackAiEnabledInput) {
      wbFeedbackAiEnabledInput.checked = Boolean(settings && settings.feedback_ai_enabled);
    }
    if (wbFeedbackAiPromptInput) {
      wbFeedbackAiPromptInput.value = String(settings?.feedback_ai_prompt || "");
    }
    updateWbFeedbackAiStatus(settings || {});
  }

  async function saveWbAutoReplySettings(event) {
    event.preventDefault();
    if (!wbAutoReplyEnabledInput || !wbAutoReplyTemplateInput) {
      return;
    }

    const payload = {
      is_enabled: wbAutoReplyEnabledInput.checked,
      answer_template: wbAutoReplyTemplateInput.value.trim(),
    };

    const response = await fetch("/admin/api/wb/auto-reply", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(body.detail || "Не удалось сохранить настройки WB");
      return;
    }

    if (body.settings) {
      hydrateWbForm(body.settings);
    } else {
      updateWbStatus(payload);
    }
  }

  async function saveWbFeedbackAiSettings(event) {
    event.preventDefault();
    if (!wbFeedbackAiEnabledInput || !wbFeedbackAiPromptInput) {
      return;
    }

    const payload = {
      feedback_ai_enabled: wbFeedbackAiEnabledInput.checked,
      feedback_ai_prompt: wbFeedbackAiPromptInput.value.trim(),
    };

    const response = await fetch("/admin/api/wb/auto-reply", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      alert(body.detail || "Не удалось сохранить AI-настройки отзывов");
      return;
    }

    if (body.settings) {
      hydrateWbForm(body.settings);
    } else {
      updateWbFeedbackAiStatus(payload);
    }
  }

  renderTemplateList();
  hydrateWbForm(initialWbAutoReply);
  refreshRenderedTimes();

  if (chatBox) {
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  if (sessionId) {
    setInterval(loadMessages, 3000);
  }

  if (chatBox) {
    chatBox.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (!target.classList.contains("message-delete-btn")) {
        return;
      }
      const messageId = target.dataset.messageId;
      if (!messageId) {
        return;
      }
      if (!confirm("Удалить сообщение у всех?")) {
        return;
      }

      target.setAttribute("disabled", "disabled");
      try {
        await deleteMessageForAll(messageId);
        const messageNode = target.closest(".message");
        if (messageNode) {
          messageNode.remove();
        }
      } catch (err) {
        target.removeAttribute("disabled");
        alert(err instanceof Error ? err.message : "Не удалось удалить сообщение");
      }
    });
  }

  if (renameUserForm) {
    renameUserForm.addEventListener("submit", async (event) => {
      try {
        await renameUser(event);
      } catch (err) {
        alert("Не удалось переименовать пользователя");
      }
    });
  }

  if (archiveUserBtn) {
    archiveUserBtn.addEventListener("click", async () => {
      const action = archiveUserBtn.dataset.archived === "true"
        ? "разархивировать"
        : "архивировать";
      if (!confirm(`Подтвердите: ${action} пользователя?`)) {
        return;
      }
      try {
        await toggleArchiveUser();
      } catch (err) {
        alert("Не удалось изменить архивный статус");
      }
    });
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

  if (wbAutoReplyForm) {
    wbAutoReplyForm.addEventListener("submit", async (event) => {
      try {
        await saveWbAutoReplySettings(event);
      } catch (err) {
        alert("Ошибка сохранения автоответа WB");
      }
    });
  }

  if (wbFeedbackAiForm) {
    wbFeedbackAiForm.addEventListener("submit", async (event) => {
      try {
        await saveWbFeedbackAiSettings(event);
      } catch (err) {
        alert("Ошибка сохранения AI-настроек отзывов");
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

      const formData = new FormData();
      formData.append("text", text);
      if (file) {
        formData.append("file", file);
      }

      const submitBtn = sendForm.querySelector('button[type="submit"]');
      messageInput.disabled = true;
      if (submitBtn instanceof HTMLButtonElement) {
        submitBtn.disabled = true;
      }

      try {
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
        if (submitBtn instanceof HTMLButtonElement) {
          submitBtn.disabled = false;
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
