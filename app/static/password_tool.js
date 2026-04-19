(function () {
  "use strict";

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const DEFAULT_SYMBOL_SET = "!@#$%&*()-_=+[]{};:,.<>?/~";
  const AMBIGUOUS_CHARS = new Set(["O", "0", "I", "l", "1", "|", "`", "'", "\""]);
  const PASSPHRASE_WORDS = [
    "anchor", "apricot", "atlas", "aurora", "bamboo", "barrel", "beacon", "berry", "birch", "blossom",
    "bonfire", "brisk", "cabin", "cactus", "canary", "canvas", "caramel", "cedar", "cinder", "citrus",
    "clover", "cobalt", "comet", "coral", "cove", "crimson", "crisp", "dahlia", "dawn", "delta",
    "ember", "falcon", "fennel", "fjord", "flint", "forest", "fossil", "garnet", "glacier", "golden",
    "harbor", "hazel", "helium", "honey", "horizon", "ivy", "juniper", "kelp", "lagoon", "lantern",
    "laurel", "linen", "lotus", "lumen", "maple", "marble", "meadow", "mercury", "meteor", "mistral",
    "nectar", "nickel", "olive", "onyx", "opal", "orchard", "otter", "paprika", "pebble", "pepper",
    "petal", "pine", "pioneer", "plume", "prairie", "quartz", "quiver", "raven", "reef", "river",
    "robin", "saffron", "sail", "sage", "scarlet", "seabird", "shadow", "silver", "sleet", "solstice",
    "spruce", "starling", "summit", "sunrise", "thistle", "timber", "topaz", "trail", "tulip", "velvet",
    "vermilion", "violet", "willow", "winter", "zephyr", "acorn", "alpine", "amber", "anthem", "arcade",
    "aster", "avalanche", "bayleaf", "beetle", "biscuit", "brook", "caper", "carousel", "cherry", "copper",
    "cotton", "coyote", "crater", "dolphin", "dragonfly", "elm", "espresso", "feather", "firefly", "ginkgo",
    "granite", "harvest", "indigo", "island", "jasmine", "kestrel", "lavender", "lemongrass", "lilac", "magnolia",
    "mallow", "mango", "mint", "moonrise", "morning", "narwhal", "nebula", "nutmeg", "oak", "oasis",
    "orchid", "pearl", "penguin", "poppy", "primrose", "pulse", "ripple", "sandstone", "sequoia", "sierra",
    "snowcap", "sparrow", "tempest", "terrace", "thyme", "torrent", "truffle", "valley", "walnut", "whisper"
  ];
  const GLOSSARY = {
    "master-password": {
      title: "マスターパスワード",
      body: "この保管庫全体を開くための合言葉です。忘れると中身を取り出せないので、長くて自分だけが分かるものにしてください。",
    },
    "kdf-iterations": {
      title: "鍵導出回数",
      body: "入力したパスワードから暗号鍵を作る計算回数です。多いほど総当たりに強くなりますが、そのぶん開閉には少し時間がかかります。",
    },
    "auto-lock": {
      title: "自動ロック",
      body: "一定時間操作がないと、復号用の鍵をブラウザのメモリから消して保管庫を閉じる仕組みです。離席時の見られ防止に役立ちます。",
    },
    passphrase: {
      title: "パスフレーズ",
      body: "複数の単語をつないで作る長い合言葉です。長さを確保しやすく、入力しやすいので、覚えやすさを重視したいときに向いています。",
    },
    "symbol-set": {
      title: "記号セット",
      body: "ランダム生成で使ってよい記号の一覧です。ここから消した記号は生成候補に入らなくなります。",
    },
    "custom-charset": {
      title: "カスタム文字セット",
      body: "ここに入力した文字だけで生成する特別モードです。通常の小文字・大文字・数字・記号の設定より優先されます。",
    },
    "api-key": {
      title: "API キー",
      body: "サービス同士の接続認証に使う長いトークンです。人が直接入力するより、自動連携や開発用途で使われることが多い秘密情報です。",
    },
    totp: {
      title: "TOTP シークレット",
      body: "30秒ごとに変わる6桁コードを作る元データです。認証アプリの中に入っている秘密鍵と同じものです。",
    },
  };

  const state = {
    endpoints: {},
    csrfToken: "",
    vault: null,
    encryptedItems: [],
    entries: [],
    vaultKey: null,
    unlocked: false,
    autoLockMinutes: 15,
    autoLockDeadline: 0,
    autoLockTimer: null,
    flashTimer: null,
    totpTimer: null,
    results: [],
    editingId: null,
    revealedIds: new Set(),
    totpKeyCache: new Map(),
    glossaryKey: "",
    glossaryTrigger: null,
    activeTab: "vault",
  };

  const els = {};

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    const root = document.getElementById("password-tool-app");
    if (!root) {
      return;
    }

    bindElements();

    if (!window.crypto || !window.crypto.subtle) {
      showFlash("このブラウザでは Web Crypto が使えません。最新の Chrome / Edge をご利用ください。", "error");
      return;
    }

    state.endpoints = {
      vaultUrl: root.dataset.vaultUrl,
      createVaultUrl: root.dataset.createVaultUrl,
      rotateVaultUrl: root.dataset.rotateVaultUrl,
      itemsUrl: root.dataset.itemsUrl,
    };

    decorateGlossaryTerms();
    bindEvents();
    restorePreferences();
    await refreshVaultState();
    renderAll();
  }

  function bindElements() {
    els.flashBanner = document.getElementById("flash-banner");
    els.vaultPill = document.getElementById("vault-pill");
    els.vaultMetaStrip = document.getElementById("vault-meta-strip");
    els.vaultSummary = document.getElementById("vault-summary");
    els.vaultSecurityNote = document.getElementById("vault-security-note");
    els.lockVaultButton = document.getElementById("lock-vault-button");
    els.autoLockMinutes = document.getElementById("auto-lock-minutes");
    els.autoLockReadout = document.getElementById("auto-lock-readout");
    els.setupPanel = document.getElementById("setup-panel");
    els.unlockPanel = document.getElementById("unlock-panel");
    els.changeMasterPanel = document.getElementById("change-master-panel");
    els.setupForm = document.getElementById("setup-form");
    els.unlockForm = document.getElementById("unlock-form");
    els.changeMasterForm = document.getElementById("change-master-form");
    els.setupMasterPassword = document.getElementById("setup-master-password");
    els.setupMasterPasswordConfirm = document.getElementById("setup-master-password-confirm");
    els.setupKdfIterations = document.getElementById("setup-kdf-iterations");
    els.unlockMasterPassword = document.getElementById("unlock-master-password");
    els.changeMasterPassword = document.getElementById("change-master-password");
    els.changeMasterPasswordConfirm = document.getElementById("change-master-password-confirm");
    els.changeKdfIterations = document.getElementById("change-kdf-iterations");
    els.generatorForm = document.getElementById("generator-form");
    els.generatorMode = document.getElementById("generator-mode");
    els.generatorRandomFields = document.getElementById("generator-random-fields");
    els.generatorPassphraseFields = document.getElementById("generator-passphrase-fields");
    els.generatorLength = document.getElementById("generator-length");
    els.generatorLower = document.getElementById("generator-lower");
    els.generatorUpper = document.getElementById("generator-upper");
    els.generatorNumbers = document.getElementById("generator-numbers");
    els.generatorSymbols = document.getElementById("generator-symbols");
    els.generatorExcludeAmbiguous = document.getElementById("generator-exclude-ambiguous");
    els.generatorRequireGroups = document.getElementById("generator-require-groups");
    els.generatorSymbolSet = document.getElementById("generator-symbol-set");
    els.generatorCustomCharset = document.getElementById("generator-custom-charset");
    els.generatorWordCount = document.getElementById("generator-word-count");
    els.generatorSeparator = document.getElementById("generator-separator");
    els.generatorPassphraseCase = document.getElementById("generator-passphrase-case");
    els.generatorPassphraseNumberAffix = document.getElementById("generator-passphrase-number-affix");
    els.generatorCount = document.getElementById("generator-count");
    els.generatorResults = document.getElementById("generator-results");
    els.entryForm = document.getElementById("entry-form");
    els.entryId = document.getElementById("entry-id");
    els.entryType = document.getElementById("entry-type");
    els.entryTitle = document.getElementById("entry-title");
    els.entryUrl = document.getElementById("entry-url");
    els.entryUsername = document.getElementById("entry-username");
    els.entryPassword = document.getElementById("entry-password");
    els.entryPasswordToggle = document.getElementById("entry-password-toggle");
    els.entryTotp = document.getElementById("entry-totp");
    els.entryTags = document.getElementById("entry-tags");
    els.entryNotes = document.getElementById("entry-notes");
    els.entrySaveButton = document.getElementById("entry-save-button");
    els.entryClearButton = document.getElementById("entry-clear-button");
    els.editorStrengthPanel = document.getElementById("editor-strength-panel");
    els.vaultWorkspace = document.getElementById("vault-workspace");
    els.healthSummary = document.getElementById("health-summary");
    els.healthFindings = document.getElementById("health-findings");
    els.vaultSearch = document.getElementById("vault-search");
    els.vaultSort = document.getElementById("vault-sort");
    els.vaultList = document.getElementById("vault-list");
    els.glossaryPopover = document.getElementById("glossary-popover");
    els.glossaryPopoverTitle = document.getElementById("glossary-popover-title");
    els.glossaryPopoverBody = document.getElementById("glossary-popover-body");
    els.glossaryPopoverClose = document.getElementById("glossary-popover-close");
    els.toolTabButtons = Array.from(document.querySelectorAll("[data-tool-tab]"));
    els.toolTabPanels = Array.from(document.querySelectorAll("[data-tool-tab-panel]"));
  }

  function bindEvents() {
    els.setupForm.addEventListener("submit", handleVaultSetup);
    els.unlockForm.addEventListener("submit", handleVaultUnlock);
    els.changeMasterForm.addEventListener("submit", handleMasterPasswordRotation);
    els.generatorForm.addEventListener("submit", handleGenerate);
    els.generatorMode.addEventListener("change", renderGeneratorMode);
    els.entryForm.addEventListener("submit", handleSaveEntry);
    els.entryClearButton.addEventListener("click", clearEntryForm);
    els.entryPasswordToggle.addEventListener("click", toggleEntryPasswordVisibility);
    els.entryPassword.addEventListener("input", renderEditorStrength);
    els.entryType.addEventListener("change", renderEditorStrength);
    els.vaultSearch.addEventListener("input", renderVaultList);
    els.vaultSort.addEventListener("change", renderVaultList);
    els.lockVaultButton.addEventListener("click", () => lockVault("手動でロックしました。", "warn"));
    els.autoLockMinutes.addEventListener("change", handleAutoLockPreferenceChange);
    els.toolTabButtons.forEach((button) => {
      button.addEventListener("click", () => switchToolTab(button.dataset.toolTab || ""));
    });
    if (els.glossaryPopoverClose) {
      els.glossaryPopoverClose.addEventListener("click", hideGlossaryPopover);
    }
    document.addEventListener("click", handleGlossaryDocumentClick);
    document.addEventListener("keydown", handleGlossaryKeydown);
    window.addEventListener("resize", hideGlossaryPopover);
    window.addEventListener("scroll", hideGlossaryPopover, true);

  }

  function decorateGlossaryTerms() {
    document.querySelectorAll(".js-glossary-term").forEach((node) => {
      const key = node.dataset.glossaryKey || "";
      const glossary = GLOSSARY[key];
      const sourceText = (node.textContent || "").trim() || glossary?.title || "";
      if (!glossary || !sourceText) {
        return;
      }
      node.classList.add("glossary-term");
      node.innerHTML = Array.from(sourceText)
        .map((char) => {
          if (char === " ") {
            return "<span class=\"glossary-term__space\" aria-hidden=\"true\"></span>";
          }
          return `<span class="glossary-term__char" role="button" tabindex="0" data-glossary-key="${escapeHtml(key)}" aria-label="${escapeHtml(glossary.title)} の説明を表示">${escapeHtml(char)}</span>`;
        })
        .join("");
    });
  }

  function handleGlossaryDocumentClick(event) {
    const trigger = event.target.closest(".glossary-term__char");
    if (trigger) {
      event.preventDefault();
      const key = trigger.dataset.glossaryKey || "";
      if (state.glossaryKey === key && state.glossaryTrigger === trigger) {
        hideGlossaryPopover();
      } else {
        showGlossaryPopover(trigger, key);
      }
      return;
    }

    if (els.glossaryPopover && els.glossaryPopover.contains(event.target)) {
      return;
    }

    hideGlossaryPopover();
  }

  function handleGlossaryKeydown(event) {
    const trigger = event.target.closest(".glossary-term__char");
    if (trigger && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      showGlossaryPopover(trigger, trigger.dataset.glossaryKey || "");
      return;
    }

    if (event.key === "Escape") {
      hideGlossaryPopover();
    }
  }

  function showGlossaryPopover(trigger, key) {
    const glossary = GLOSSARY[key];
    if (!glossary || !els.glossaryPopover || !els.glossaryPopoverTitle || !els.glossaryPopoverBody) {
      return;
    }

    state.glossaryKey = key;
    state.glossaryTrigger = trigger;
    els.glossaryPopover.hidden = false;
    els.glossaryPopoverTitle.textContent = glossary.title;
    els.glossaryPopoverBody.textContent = glossary.body;
    document.querySelectorAll(".glossary-term").forEach((node) => {
      node.classList.toggle("glossary-term--active", node.contains(trigger));
    });
    positionGlossaryPopover(trigger);
  }

  function positionGlossaryPopover(trigger) {
    if (!els.glossaryPopover || !trigger) {
      return;
    }

    const margin = 12;
    const offset = 14;
    const rect = trigger.getBoundingClientRect();
    const popoverWidth = els.glossaryPopover.offsetWidth;
    const popoverHeight = els.glossaryPopover.offsetHeight;
    let left = rect.left + rect.width / 2 - popoverWidth / 2;
    left = Math.max(margin, Math.min(window.innerWidth - popoverWidth - margin, left));

    let top = rect.bottom + offset;
    let placement = "bottom";
    if (top + popoverHeight > window.innerHeight - margin) {
      top = rect.top - popoverHeight - offset;
      placement = "top";
    }
    top = Math.max(margin, top);

    els.glossaryPopover.dataset.placement = placement;
    els.glossaryPopover.style.left = `${left}px`;
    els.glossaryPopover.style.top = `${top}px`;
  }

  function hideGlossaryPopover() {
    if (!els.glossaryPopover) {
      return;
    }
    els.glossaryPopover.hidden = true;
    state.glossaryKey = "";
    state.glossaryTrigger = null;
    document.querySelectorAll(".glossary-term--active").forEach((node) => {
      node.classList.remove("glossary-term--active");
    });
  }

  function restorePreferences() {
    const savedMinutes = Number.parseInt(localStorage.getItem("dstt-vault-auto-lock-minutes") || "", 10);
    if (Number.isFinite(savedMinutes) && savedMinutes >= 1 && savedMinutes <= 240) {
      state.autoLockMinutes = savedMinutes;
    }
    const savedTab = localStorage.getItem("dstt-vault-active-tab") || "vault";
    if (["vault", "generator", "editor", "list"].includes(savedTab)) {
      state.activeTab = savedTab;
    }
    els.autoLockMinutes.value = String(state.autoLockMinutes);
    els.changeKdfIterations.value = els.setupKdfIterations.value || "600000";
  }

  async function refreshVaultState() {
    const payload = await apiFetch(state.endpoints.vaultUrl, { method: "GET" });
    state.csrfToken = payload.csrf_token || "";
    state.vault = payload.vault;
    state.encryptedItems = Array.isArray(payload.items) ? payload.items : [];
    if (!state.unlocked) {
      state.entries = [];
      state.revealedIds.clear();
      state.totpKeyCache.clear();
    }
  }

  async function handleVaultSetup(event) {
    event.preventDefault();

    const masterPassword = els.setupMasterPassword.value;
    const masterPasswordConfirm = els.setupMasterPasswordConfirm.value;
    const iterations = clampInteger(els.setupKdfIterations.value, 200000, 2000000, 600000);
    els.setupKdfIterations.value = String(iterations);

    if (masterPassword !== masterPasswordConfirm) {
      showFlash("確認用のマスターパスワードが一致しません。", "error");
      return;
    }

    try {
      const { key, payload } = await buildVaultCreationPayload(masterPassword, iterations);
      const response = await apiFetch(state.endpoints.createVaultUrl, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.vault = response.vault;
      state.encryptedItems = response.items || [];
      state.vaultKey = key;
      state.unlocked = true;
      state.entries = [];
      state.editingId = null;
      els.unlockMasterPassword.value = "";
      els.setupForm.reset();
      els.setupKdfIterations.value = "600000";
      els.changeKdfIterations.value = String(state.vault.kdf_iterations);
      onUnlocked();
      showFlash("保管庫を作成しました。以後はブラウザ内でのみ復号します。", "success");
    } catch (error) {
      showFlash(error.message || "保管庫の作成に失敗しました。", "error");
    }
  }

  async function handleVaultUnlock(event) {
    event.preventDefault();

    if (!state.vault) {
      showFlash("先に保管庫を作成してください。", "warn");
      return;
    }

    const masterPassword = els.unlockMasterPassword.value;
    try {
      const vaultKey = await deriveVaultKey(masterPassword, state.vault.salt_b64, state.vault.kdf_iterations);
      await decryptJsonPayload(
        {
          nonce_b64: state.vault.check_nonce_b64,
          ciphertext_b64: state.vault.check_ciphertext_b64,
        },
        vaultKey
      );

      state.vaultKey = vaultKey;
      state.entries = await Promise.all(state.encryptedItems.map((item) => decryptStoredItem(item, vaultKey)));
      state.unlocked = true;
      state.editingId = null;
      state.revealedIds.clear();
      state.totpKeyCache.clear();
      els.unlockForm.reset();
      els.changeKdfIterations.value = String(state.vault.kdf_iterations || 600000);
      onUnlocked();
      showFlash("保管庫をアンロックしました。", "success");
    } catch (error) {
      showFlash("マスターパスワードが正しくありません。", "error");
      lockVault();
    }
  }

  async function handleMasterPasswordRotation(event) {
    event.preventDefault();

    if (!state.unlocked || !state.vaultKey || !state.vault) {
      showFlash("先に保管庫をアンロックしてください。", "warn");
      return;
    }

    const masterPassword = els.changeMasterPassword.value;
    const confirmPassword = els.changeMasterPasswordConfirm.value;
    const iterations = clampInteger(els.changeKdfIterations.value, 200000, 2000000, 600000);
    els.changeKdfIterations.value = String(iterations);

    if (masterPassword !== confirmPassword) {
      showFlash("確認用のマスターパスワードが一致しません。", "error");
      return;
    }

    try {
      const { key, payload } = await buildVaultCreationPayload(masterPassword, iterations);
      const encryptedItems = await Promise.all(
        state.entries.map(async (entry) => {
          const encrypted = await encryptJsonPayload(serializeEntry(entry), key);
          return {
            id: entry.id,
            schema_version: 1,
            nonce_b64: encrypted.nonce_b64,
            ciphertext_b64: encrypted.ciphertext_b64,
          };
        })
      );

      const response = await apiFetch(state.endpoints.rotateVaultUrl, {
        method: "POST",
        body: JSON.stringify({ ...payload, items: encryptedItems }),
      });

      state.vault = response.vault;
      state.encryptedItems = response.items || [];
      state.vaultKey = key;
      state.entries = await Promise.all(state.encryptedItems.map((item) => decryptStoredItem(item, key)));
      state.unlocked = true;
      els.changeMasterForm.reset();
      els.changeKdfIterations.value = String(state.vault.kdf_iterations);
      onUnlocked();
      showFlash("マスターパスワードを更新しました。", "success");
    } catch (error) {
      showFlash(error.message || "マスターパスワードの更新に失敗しました。", "error");
    }
  }

  function handleGenerate(event) {
    event.preventDefault();

    try {
      state.results = generateSecrets();
      renderGeneratorResults();
      showFlash("条件に合わせて秘密情報を生成しました。", "success");
    } catch (error) {
      showFlash(error.message || "生成条件が不正です。", "error");
    }
  }

  async function handleSaveEntry(event) {
    event.preventDefault();

    if (!state.unlocked || !state.vaultKey) {
      showFlash("保管庫をアンロックすると保存できます。", "warn");
      return;
    }

    const existingEntry = state.entries.find((entry) => entry.id === Number.parseInt(els.entryId.value || "", 10)) || null;
    const entry = buildEntryFromForm(existingEntry);

    try {
      const encrypted = await encryptJsonPayload(serializeEntry(entry), state.vaultKey);
      if (existingEntry) {
        const response = await apiFetch(`${state.endpoints.itemsUrl}/${existingEntry.id}`, {
          method: "PUT",
          body: JSON.stringify({
            schema_version: 1,
            nonce_b64: encrypted.nonce_b64,
            ciphertext_b64: encrypted.ciphertext_b64,
          }),
        });
        updateStoredItem(response.item);
        entry.id = response.item.id;
      } else {
        const response = await apiFetch(state.endpoints.itemsUrl, {
          method: "POST",
          body: JSON.stringify({
            schema_version: 1,
            nonce_b64: encrypted.nonce_b64,
            ciphertext_b64: encrypted.ciphertext_b64,
          }),
        });
        entry.id = response.item.id;
        state.encryptedItems.unshift(response.item);
      }

      const hydrated = hydrateEntry(entry);
      upsertEntry(hydrated);
      clearEntryForm();
      renderAll();
      showFlash(existingEntry ? "資格情報を更新しました。" : "資格情報を保存しました。", "success");
    } catch (error) {
      showFlash(error.message || "資格情報の保存に失敗しました。", "error");
    }
  }

  function handleAutoLockPreferenceChange() {
    state.autoLockMinutes = clampInteger(els.autoLockMinutes.value, 1, 240, 15);
    els.autoLockMinutes.value = String(state.autoLockMinutes);
    localStorage.setItem("dstt-vault-auto-lock-minutes", String(state.autoLockMinutes));
    touchSession();
    renderVaultSummary();
  }

  function toggleEntryPasswordVisibility() {
    const nextType = els.entryPassword.type === "password" ? "text" : "password";
    els.entryPassword.type = nextType;
    els.entryPasswordToggle.textContent = nextType === "password" ? "表示" : "非表示";
  }

  function clearEntryForm() {
    els.entryForm.reset();
    els.entryId.value = "";
    els.entryPassword.type = "password";
    els.entryPasswordToggle.textContent = "表示";
    state.editingId = null;
    renderEditorStrength();
  }

  function touchSession() {
    if (!state.unlocked) {
      renderAutoLockCountdown();
      return;
    }
    state.autoLockDeadline = Date.now() + state.autoLockMinutes * 60 * 1000;
    renderAutoLockCountdown();
  }

  function onUnlocked() {
    touchSession();
    startTimers();
    renderAll();
  }

  function startTimers() {
    window.clearInterval(state.autoLockTimer);
    state.autoLockTimer = window.setInterval(() => {
      if (!state.unlocked) {
        renderAutoLockCountdown();
        return;
      }
      if (Date.now() >= state.autoLockDeadline) {
        lockVault("一定時間操作がなかったためロックしました。", "warn");
        return;
      }
      renderAutoLockCountdown();
    }, 1000);

    window.clearInterval(state.totpTimer);
    state.totpTimer = window.setInterval(() => {
      if (state.unlocked) {
        refreshVisibleTotpCodes();
      }
      renderAutoLockCountdown();
    }, 1000);
  }

  function lockVault(message, variant) {
    state.unlocked = false;
    state.vaultKey = null;
    state.entries = [];
    state.revealedIds.clear();
    state.totpKeyCache.clear();
    state.editingId = null;
    if (state.activeTab !== "vault") {
      state.activeTab = "vault";
    }
    hideGlossaryPopover();
    clearEntryForm();
    renderAll();
    if (message) {
      showFlash(message, variant || "warn");
    }
  }

  function renderAll() {
    renderToolTabs();
    renderGeneratorMode();
    renderVaultSummary();
    renderAuthPanels();
    renderGeneratorResults();
    renderEditorStrength();
    renderVaultList();
    renderHealth();
    renderAutoLockCountdown();
  }

  function renderGeneratorMode() {
    const passphraseMode = els.generatorMode.value === "passphrase";
    els.generatorRandomFields.hidden = passphraseMode;
    els.generatorPassphraseFields.hidden = !passphraseMode;
    els.generatorForm.dataset.mode = passphraseMode ? "passphrase" : "random";
  }

  function isVaultReady() {
    return Boolean(state.vault) && state.unlocked;
  }

  function renderToolTabs() {
    const ready = isVaultReady();
    els.toolTabButtons.forEach((button) => {
      const tab = button.dataset.toolTab || "";
      const locked = !ready && tab !== "vault";
      const active = button.dataset.toolTab === state.activeTab;
      button.classList.toggle("is-active", active);
      button.classList.toggle("is-locked", locked);
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.setAttribute("aria-disabled", locked ? "true" : "false");
      button.disabled = locked;
    });
    els.toolTabPanels.forEach((panel) => {
      panel.hidden = panel.dataset.toolTabPanel !== state.activeTab;
    });
  }

  function switchToolTab(tabName, options) {
    if (!["vault", "generator", "editor", "list"].includes(tabName)) {
      return;
    }
    if (!isVaultReady() && tabName !== "vault") {
      showFlash("先に保管庫を作成してアンロックしてください。", "warn");
      tabName = "vault";
    }
    state.activeTab = tabName;
    if (!options || options.persist !== false) {
      localStorage.setItem("dstt-vault-active-tab", tabName);
    }
    hideGlossaryPopover();
    renderToolTabs();
  }

  function renderVaultSummary() {
    if (!state.unlocked) {
      els.vaultMetaStrip.hidden = true;
      els.vaultPill.textContent = state.vault ? "ロック中" : "未作成";
      els.vaultPill.dataset.state = state.vault ? "locked" : "missing";
      els.lockVaultButton.disabled = true;
      return;
    }

    const health = computeVaultHealth(state.entries);
    els.vaultMetaStrip.hidden = false;
    els.vaultPill.textContent = "アンロック中";
    els.vaultPill.dataset.state = "unlocked";
    els.lockVaultButton.disabled = false;
    els.vaultSummary.innerHTML = [
      statCard("保存件数", String(state.entries.length)),
      statCard("重複パスワード", String(health.duplicateCount)),
      statCard("要注意", String(health.weakCount + health.staleCount)),
      statCard("TOTP", String(health.totpCount)),
    ].join("");
    els.vaultSecurityNote.hidden = true;
  }

  function renderAuthPanels() {
    const vaultExists = Boolean(state.vault);
    els.setupPanel.hidden = vaultExists;
    els.unlockPanel.hidden = !vaultExists || state.unlocked;
    els.changeMasterPanel.hidden = !vaultExists || !state.unlocked;
    els.entrySaveButton.disabled = !state.unlocked;
  }

  function renderGeneratorResults() {
    if (!state.results.length) {
      els.generatorResults.className = "result-list empty-state";
      els.generatorResults.textContent = "まだ生成していません。";
      return;
    }

    els.generatorResults.className = "result-list";
    els.generatorResults.innerHTML = state.results
      .map((result, index) => {
        const profile = describeSecretProfile(result.secret);
        return `
          <article class="result-card strength-card" data-score="${result.strength.score}">
            <div class="result-card__head">
              <div>
                <p class="result-card__eyebrow">候補 ${index + 1}</p>
                <p class="result-card__title">${escapeHtml(result.strength.label)}</p>
              </div>
              <span class="status-badge" data-tone="${result.strength.score}">推定 ${result.strength.bits} bits</span>
            </div>
            <div class="result-card__secret">${escapeHtml(result.secret)}</div>
            <div class="meta-pills">
              <span class="meta-pill">${escapeHtml(profile.lengthLabel)}</span>
              <span class="meta-pill">${escapeHtml(profile.composition)}</span>
            </div>
            <p class="result-card__meta">${escapeHtml(result.strength.detail)}</p>
            <div class="result-card__actions">
              <button class="button button--primary" type="button" data-action="copy-generated" data-index="${index}">コピー</button>
              <button class="button button--secondary" type="button" data-action="use-generated" data-index="${index}">入力欄へ使う</button>
            </div>
          </article>
        `;
      })
      .join("");

    els.generatorResults.querySelectorAll("[data-action='copy-generated']").forEach((button) => {
      button.addEventListener("click", () => copyToClipboard(state.results[Number(button.dataset.index)].secret, "生成結果をコピーしました。"));
    });
    els.generatorResults.querySelectorAll("[data-action='use-generated']").forEach((button) => {
      button.addEventListener("click", () => {
        const secret = state.results[Number(button.dataset.index)].secret;
        els.entryPassword.value = secret;
        renderEditorStrength();
        switchToolTab("editor", { persist: false });
        showFlash("生成結果を入力欄に反映しました。", "success");
      });
    });
  }

  function renderEditorStrength() {
    const strength = scoreSecret(els.entryPassword.value, els.entryType.value);
    if (!els.entryPassword.value) {
      els.editorStrengthPanel.className = "strength-panel empty-state";
      els.editorStrengthPanel.textContent = "秘密情報を入力すると強度を表示します。";
      return;
    }

    els.editorStrengthPanel.className = "strength-panel";
    els.editorStrengthPanel.innerHTML = `
      <div class="strength-card" data-score="${strength.score}">
        <div class="strength-card__head">
          <p class="result-card__title">${escapeHtml(strength.label)}</p>
          <span class="status-badge" data-tone="${strength.score}">${strength.bits ? `推定 ${strength.bits} bits` : "内容メモ"}</span>
        </div>
        <div class="strength-meter"><span style="width: ${strengthProgress(strength)}%;"></span></div>
        <p class="result-card__meta">${escapeHtml(strength.detail)}</p>
        <p class="strength-card__tip">${escapeHtml(strengthAdvice(strength, els.entryType.value))}</p>
      </div>
    `;
  }

  function renderVaultList() {
    if (!state.unlocked) {
      els.vaultList.className = "vault-list empty-state";
      els.vaultList.textContent = "保管庫をアンロックすると内容を表示します。";
      return;
    }

    const filteredEntries = getVisibleEntries();
    if (!filteredEntries.length) {
      els.vaultList.className = "vault-list empty-state";
      els.vaultList.textContent = "条件に一致する資格情報がありません。";
      return;
    }

    els.vaultList.className = "vault-list";
    els.vaultList.innerHTML = filteredEntries
      .map((entry) => {
        const strength = scoreSecret(entry.secret, entry.secretType);
        const revealed = state.revealedIds.has(entry.id);
        const secretToShow = revealed ? entry.secret : maskSecret(entry.secret);
        const safeUrl = safeExternalUrl(entry.url);
        const totpBlock = entry.totpSecret
          ? `
            <div class="totp-panel">
              <span class="totp-panel__label">ワンタイムコード</span>
              <div class="vault-card__code">
                <span class="js-totp-code" data-entry-id="${entry.id}">------</span>
                <span class="muted js-totp-remaining" data-entry-id="${entry.id}">30s</span>
              </div>
            </div>
          `
          : "";
        const historyText = entry.history.length ? `過去 ${entry.history.length} 件の変更履歴` : "履歴なし";
        const tagBlock = entry.tags.length
          ? `
            <div class="meta-pills">
              ${entry.tags.map((tag) => `<span class="meta-pill meta-pill--tag">${escapeHtml(tag)}</span>`).join("")}
            </div>
          `
          : "";
        return `
          <article class="vault-card strength-card" data-score="${strength.score}">
            <div class="vault-card__head">
              <div>
                <p class="vault-card__eyebrow">更新 ${escapeHtml(formatDate(entry.updatedAt))}</p>
                <p class="vault-card__title">${escapeHtml(entry.title || "無題の資格情報")}</p>
              </div>
              <span class="status-badge" data-tone="${strength.score}">${escapeHtml(secretTypeLabel(entry.secretType))}</span>
            </div>
            <div class="meta-pills">
              <span class="meta-pill">${escapeHtml(entry.username || "ユーザー名未設定")}</span>
              <span class="meta-pill">${escapeHtml(historyText)}</span>
              ${strength.bits ? `<span class="meta-pill">推定 ${strength.bits} bits</span>` : ""}
              ${safeUrl ? `<a class="meta-pill meta-pill--link" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">URL を開く</a>` : ""}
            </div>
            <div class="vault-card__secret">${escapeHtml(secretToShow)}</div>
            ${totpBlock}
            ${tagBlock}
            ${entry.url && !safeUrl ? `<p class="vault-card__meta">${escapeHtml(entry.url)}</p>` : ""}
            ${entry.notes ? `<p class="vault-card__note">${escapeHtml(truncateText(entry.notes, 120))}</p>` : ""}
            <div class="vault-card__actions">
              <button class="button button--primary" type="button" data-action="copy-secret" data-id="${entry.id}">コピー</button>
              <button class="button button--secondary" type="button" data-action="toggle-reveal" data-id="${entry.id}">${revealed ? "隠す" : "表示"}</button>
              <button class="button button--secondary" type="button" data-action="edit-entry" data-id="${entry.id}">編集</button>
              <button class="button button--ghost" type="button" data-action="delete-entry" data-id="${entry.id}">削除</button>
            </div>
          </article>
        `;
      })
      .join("");

    bindVaultListActions();
    refreshVisibleTotpCodes();
  }

  function renderHealth() {
    if (!state.unlocked) {
      els.healthSummary.innerHTML = "";
      els.healthFindings.className = "finding-list empty-state";
      els.healthFindings.textContent = "保管庫をアンロックすると診断結果を表示します。";
      return;
    }

    const health = computeVaultHealth(state.entries);
    els.healthSummary.innerHTML = [
      healthChip("重複", health.duplicateCount),
      healthChip("弱い", health.weakCount),
      healthChip("長期未更新", health.staleCount),
      healthChip("TOTP", health.totpCount),
    ].join("");

    const findings = [];
    if (health.duplicateGroups.length) {
      const labels = health.duplicateGroups
        .slice(0, 3)
        .map((group) => group.map((entry) => entry.title || "無題").join(" / "));
      findings.push({
        level: "critical",
        title: "同じ秘密情報の使い回しがあります",
        text: `${health.duplicateCount} 件が重複しています。例: ${labels.join("、")}`,
      });
    }
    if (health.weakEntries.length) {
      const sample = health.weakEntries
        .slice(0, 3)
        .map((entry) => entry.title || "無題")
        .join("、");
      findings.push({
        level: "warn",
        title: "強度が不足している項目があります",
        text: `${health.weakCount} 件が要注意です。例: ${sample}`,
      });
    }
    if (health.staleEntries.length) {
      const sample = health.staleEntries
        .slice(0, 3)
        .map((entry) => `${entry.title || "無題"} (${daysSince(entry.updatedAt)}日)`)
        .join("、");
      findings.push({
        level: "warn",
        title: "長期間更新されていない項目があります",
        text: `${health.staleCount} 件が180日以上未更新です。例: ${sample}`,
      });
    }
    if (!findings.length) {
      findings.push({
        level: "good",
        title: "大きな警告は見つかっていません",
        text: "現時点では、重複・強度不足・長期未更新の主要な警告はありません。",
      });
    }

    els.healthFindings.className = "finding-list";
    els.healthFindings.innerHTML = findings
      .map((finding) => {
        return `
          <article class="finding" data-level="${finding.level}">
            <p class="finding__title">${escapeHtml(finding.title)}</p>
            <p class="finding__text">${escapeHtml(finding.text)}</p>
          </article>
        `;
      })
      .join("");
  }

  function renderAutoLockCountdown() {
    if (!state.unlocked || !state.autoLockDeadline) {
      els.autoLockReadout.textContent = "--:--";
      return;
    }
    const remaining = Math.max(0, Math.ceil((state.autoLockDeadline - Date.now()) / 1000));
    const minutes = String(Math.floor(remaining / 60)).padStart(2, "0");
    const seconds = String(remaining % 60).padStart(2, "0");
    els.autoLockReadout.textContent = `${minutes}:${seconds}`;
  }

  function bindVaultListActions() {
    els.vaultList.querySelectorAll("[data-action='copy-secret']").forEach((button) => {
      button.addEventListener("click", async () => {
        const entry = findEntry(button.dataset.id);
        if (entry) {
          await copyToClipboard(entry.secret, "秘密情報をコピーしました。");
        }
      });
    });

    els.vaultList.querySelectorAll("[data-action='toggle-reveal']").forEach((button) => {
      button.addEventListener("click", () => {
        const entryId = Number.parseInt(button.dataset.id, 10);
        if (state.revealedIds.has(entryId)) {
          state.revealedIds.delete(entryId);
        } else {
          state.revealedIds.add(entryId);
        }
        renderVaultList();
      });
    });

    els.vaultList.querySelectorAll("[data-action='edit-entry']").forEach((button) => {
      button.addEventListener("click", () => {
        const entry = findEntry(button.dataset.id);
        if (!entry) {
          return;
        }
        state.editingId = entry.id;
        els.entryId.value = String(entry.id);
        els.entryType.value = entry.secretType;
        els.entryTitle.value = entry.title;
        els.entryUrl.value = entry.url;
        els.entryUsername.value = entry.username;
        els.entryPassword.value = entry.secret;
        els.entryTotp.value = entry.totpSecret;
        els.entryTags.value = entry.tags.join(", ");
        els.entryNotes.value = entry.notes;
        renderEditorStrength();
        switchToolTab("editor", { persist: false });
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });

    els.vaultList.querySelectorAll("[data-action='delete-entry']").forEach((button) => {
      button.addEventListener("click", async () => {
        const entry = findEntry(button.dataset.id);
        if (!entry) {
          return;
        }
        if (!window.confirm(`「${entry.title || "無題の資格情報"}」を削除しますか？`)) {
          return;
        }
        try {
          await apiFetch(`${state.endpoints.itemsUrl}/${entry.id}`, { method: "DELETE" });
          state.entries = state.entries.filter((item) => item.id !== entry.id);
          state.encryptedItems = state.encryptedItems.filter((item) => item.id !== entry.id);
          state.revealedIds.delete(entry.id);
          if (state.editingId === entry.id) {
            clearEntryForm();
          }
          renderAll();
          showFlash("資格情報を削除しました。", "success");
        } catch (error) {
          showFlash(error.message || "削除に失敗しました。", "error");
        }
      });
    });
  }

  async function refreshVisibleTotpCodes() {
    const codeNodes = document.querySelectorAll(".js-totp-code");
    for (const node of codeNodes) {
      const entry = findEntry(node.dataset.entryId);
      if (!entry || !entry.totpSecret) {
        node.textContent = "------";
        continue;
      }
      try {
        node.textContent = await generateTotpCode(entry.totpSecret);
        const remainingNode = document.querySelector(`.js-totp-remaining[data-entry-id="${entry.id}"]`);
        if (remainingNode) {
          const remaining = 30 - (Math.floor(Date.now() / 1000) % 30);
          remainingNode.textContent = `${remaining}s`;
        }
      } catch (_error) {
        node.textContent = "Invalid";
      }
    }
  }

  function findEntry(entryId) {
    const numericId = Number.parseInt(entryId, 10);
    return state.entries.find((entry) => entry.id === numericId) || null;
  }

  function updateStoredItem(serverItem) {
    const nextItems = state.encryptedItems.filter((item) => item.id !== serverItem.id);
    nextItems.unshift(serverItem);
    state.encryptedItems = nextItems;
  }

  function upsertEntry(entry) {
    const nextEntries = state.entries.filter((item) => item.id !== entry.id);
    nextEntries.unshift(entry);
    state.entries = nextEntries;
  }

  function getVisibleEntries() {
    const search = (els.vaultSearch.value || "").trim().toLowerCase();
    const sortBy = els.vaultSort.value;
    let entries = state.entries.slice();

    if (search) {
      entries = entries.filter((entry) => {
        const haystack = [
          entry.title,
          entry.url,
          entry.username,
          entry.notes,
          entry.tags.join(" "),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(search);
      });
    }

    entries.sort((left, right) => {
      if (sortBy === "updated_asc") {
        return Date.parse(left.updatedAt) - Date.parse(right.updatedAt);
      }
      if (sortBy === "title_asc") {
        return left.title.localeCompare(right.title, "ja");
      }
      if (sortBy === "created_desc") {
        return Date.parse(right.createdAt) - Date.parse(left.createdAt);
      }
      return Date.parse(right.updatedAt) - Date.parse(left.updatedAt);
    });

    return entries;
  }

  function buildEntryFromForm(existingEntry) {
    const now = new Date().toISOString();
    const secret = els.entryPassword.value || "";
    let history = existingEntry ? existingEntry.history.slice() : [];

    if (existingEntry && existingEntry.secret && existingEntry.secret !== secret) {
      history.unshift({
        value: existingEntry.secret,
        changedAt: now,
      });
      history = history.slice(0, 12);
    }

    return hydrateEntry({
      id: existingEntry ? existingEntry.id : null,
      secretType: els.entryType.value,
      title: (els.entryTitle.value || "").trim(),
      url: (els.entryUrl.value || "").trim(),
      username: (els.entryUsername.value || "").trim(),
      secret,
      totpSecret: normalizeTotpSecret(els.entryTotp.value || ""),
      tags: splitTags(els.entryTags.value),
      notes: (els.entryNotes.value || "").trim(),
      createdAt: existingEntry ? existingEntry.createdAt : now,
      updatedAt: now,
      history,
    });
  }

  function hydrateEntry(raw) {
    return {
      id: raw.id,
      secretType: raw.secretType || "password",
      title: raw.title || "",
      url: raw.url || "",
      username: raw.username || "",
      secret: raw.secret || "",
      totpSecret: raw.totpSecret || "",
      tags: Array.isArray(raw.tags) ? raw.tags : [],
      notes: raw.notes || "",
      createdAt: raw.createdAt || new Date().toISOString(),
      updatedAt: raw.updatedAt || new Date().toISOString(),
      history: Array.isArray(raw.history) ? raw.history : [],
    };
  }

  function serializeEntry(entry) {
    return {
      version: 1,
      secretType: entry.secretType,
      title: entry.title,
      url: entry.url,
      username: entry.username,
      secret: entry.secret,
      totpSecret: entry.totpSecret,
      tags: entry.tags,
      notes: entry.notes,
      createdAt: entry.createdAt,
      updatedAt: entry.updatedAt,
      history: entry.history,
    };
  }

  async function decryptStoredItem(item, key) {
    const payload = await decryptJsonPayload(item, key);
    return hydrateEntry({
      id: item.id,
      secretType: payload.secretType,
      title: payload.title,
      url: payload.url,
      username: payload.username,
      secret: payload.secret,
      totpSecret: payload.totpSecret,
      tags: payload.tags,
      notes: payload.notes,
      createdAt: payload.createdAt || item.created_at,
      updatedAt: payload.updatedAt || item.updated_at,
      history: payload.history,
    });
  }

  function generateSecrets() {
    const mode = els.generatorMode.value;
    const count = clampInteger(els.generatorCount.value, 1, 12, 5);
    els.generatorCount.value = String(count);

    const results = [];
    for (let index = 0; index < count; index += 1) {
      const secret = mode === "passphrase" ? generatePassphrase() : generateRandomSecret();
      results.push({
        secret,
        strength: scoreSecret(secret, mode === "passphrase" ? "password" : els.entryType.value),
      });
    }
    return results;
  }

  function generateRandomSecret() {
    const customCharset = uniqueCharacters(els.generatorCustomCharset.value || "");
    const includeLower = els.generatorLower.checked;
    const includeUpper = els.generatorUpper.checked;
    const includeNumbers = els.generatorNumbers.checked;
    const includeSymbols = els.generatorSymbols.checked;
    const excludeAmbiguous = els.generatorExcludeAmbiguous.checked;
    const requireGroups = els.generatorRequireGroups.checked;
    const symbolSet = uniqueCharacters(els.generatorSymbolSet.value || DEFAULT_SYMBOL_SET);
    const length = clampInteger(els.generatorLength.value, 4, 256, 24);
    els.generatorLength.value = String(length);

    let groups = [];
    if (customCharset) {
      groups = [customCharset];
    } else {
      if (includeLower) {
        groups.push("abcdefghijklmnopqrstuvwxyz");
      }
      if (includeUpper) {
        groups.push("ABCDEFGHIJKLMNOPQRSTUVWXYZ");
      }
      if (includeNumbers) {
        groups.push("0123456789");
      }
      if (includeSymbols) {
        groups.push(symbolSet || DEFAULT_SYMBOL_SET);
      }
    }

    groups = groups
      .map((group) => (excludeAmbiguous ? filterAmbiguous(group) : group))
      .map(uniqueCharacters)
      .filter(Boolean);

    if (!groups.length) {
      throw new Error("ランダム生成に使う文字セットが空です。");
    }

    const mergedCharset = uniqueCharacters(groups.join(""));
    const characters = [];

    if (requireGroups && customCharset === "") {
      groups.forEach((group) => {
        characters.push(sampleChar(group));
      });
    }

    while (characters.length < length) {
      characters.push(sampleChar(mergedCharset));
    }

    shuffleInPlace(characters);
    return characters.slice(0, length).join("");
  }

  function generatePassphrase() {
    const wordCount = clampInteger(els.generatorWordCount.value, 3, 12, 6);
    const separator = (els.generatorSeparator.value || "-").slice(0, 4);
    const casing = els.generatorPassphraseCase.value;
    const includeNumberAffix = els.generatorPassphraseNumberAffix.checked;
    els.generatorWordCount.value = String(wordCount);

    const words = [];
    for (let index = 0; index < wordCount; index += 1) {
      let word = PASSPHRASE_WORDS[randomInt(PASSPHRASE_WORDS.length)];
      if (casing === "title") {
        word = word.slice(0, 1).toUpperCase() + word.slice(1);
      } else if (casing === "upper") {
        word = word.toUpperCase();
      }
      words.push(word);
    }

    let passphrase = words.join(separator);
    if (includeNumberAffix) {
      passphrase = `${passphrase}${separator}${String(randomInt(10000)).padStart(4, "0")}`;
    }
    return passphrase;
  }

  function computeVaultHealth(entries) {
    const duplicateMap = new Map();
    const weakEntries = [];
    const staleEntries = [];
    let totpCount = 0;

    entries.forEach((entry) => {
      if (entry.totpSecret) {
        totpCount += 1;
      }
      if (entry.secretType === "password" && entry.secret) {
        if (!duplicateMap.has(entry.secret)) {
          duplicateMap.set(entry.secret, []);
        }
        duplicateMap.get(entry.secret).push(entry);
      }
      const strength = scoreSecret(entry.secret, entry.secretType);
      if (strength.score !== "good") {
        weakEntries.push(entry);
      }
      if (daysSince(entry.updatedAt) >= 180) {
        staleEntries.push(entry);
      }
    });

    const duplicateGroups = Array.from(duplicateMap.values()).filter((group) => group.length > 1);
    const duplicateCount = duplicateGroups.reduce((total, group) => total + group.length, 0);

    return {
      duplicateGroups,
      duplicateCount,
      weakEntries,
      weakCount: weakEntries.length,
      staleEntries,
      staleCount: staleEntries.length,
      totpCount,
    };
  }

  function scoreSecret(secret, secretType) {
    if (!secret) {
      return {
        score: "critical",
        label: "未設定",
        bits: 0,
        detail: "秘密情報が空です。",
      };
    }

    if (secretType === "secure-note") {
      return {
        score: "good",
        label: "メモ",
        bits: 0,
        detail: "セキュアメモは長文保存向けです。公開されて困る内容だけを入れてください。",
      };
    }

    let charset = 0;
    let groups = 0;
    if (/[a-z]/.test(secret)) {
      charset += 26;
      groups += 1;
    }
    if (/[A-Z]/.test(secret)) {
      charset += 26;
      groups += 1;
    }
    if (/\d/.test(secret)) {
      charset += 10;
      groups += 1;
    }
    if (/[^A-Za-z0-9]/.test(secret)) {
      charset += 33;
      groups += 1;
    }
    if (charset === 0) {
      charset = 10;
    }

    const bits = Math.round(secret.length * Math.log2(charset));
    if (secret.length >= 18 && groups >= 3 && bits >= 90) {
      return {
        score: "good",
        label: "強い",
        bits,
        detail: "長さと文字種のバランスが良好です。",
      };
    }
    if (secret.length >= 12 && groups >= 2 && bits >= 60) {
      return {
        score: "warn",
        label: "要確認",
        bits,
        detail: "実用水準ですが、より長くすると安心です。",
      };
    }
    return {
      score: "critical",
      label: "弱い",
      bits,
      detail: "長さや文字種が不足しています。",
    };
  }

  function describeSecretProfile(secret) {
    const parts = [];
    if (/[a-z]/.test(secret)) {
      parts.push("小文字");
    }
    if (/[A-Z]/.test(secret)) {
      parts.push("大文字");
    }
    if (/\d/.test(secret)) {
      parts.push("数字");
    }
    if (/[^A-Za-z0-9]/.test(secret)) {
      parts.push("記号");
    }
    return {
      lengthLabel: `${Array.from(String(secret || "")).length}文字`,
      composition: parts.join("・") || "単一構成",
    };
  }

  function strengthProgress(strength) {
    if (strength.score === "good") {
      return Math.max(74, Math.min(100, strength.bits));
    }
    if (strength.score === "warn") {
      return Math.max(44, Math.min(78, Math.round(strength.bits * 0.82)));
    }
    return Math.max(16, Math.min(48, Math.round(strength.bits * 0.58)));
  }

  function strengthAdvice(strength, secretType) {
    if (secretType === "secure-note") {
      return "長文メモ向けです。タイトルとタグを付けると、あとで探しやすくなります。";
    }
    if (strength.score === "good") {
      return "このまま保存して問題ありません。別サービスでの使い回しだけ避けるとさらに安心です。";
    }
    if (strength.score === "warn") {
      return "実用はできますが、18文字前後まで伸ばすか文字種を増やすとより強くなります。";
    }
    return "長さを増やし、小文字・大文字・数字・記号のうち3種類以上を混ぜるのがおすすめです。";
  }

  function secretTypeLabel(secretType) {
    if (secretType === "api-key") {
      return "APIキー";
    }
    if (secretType === "secure-note") {
      return "セキュアメモ";
    }
    return "パスワード";
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(value);
      if (/^https?:$/i.test(url.protocol)) {
        return url.toString();
      }
    } catch (_error) {
      return "";
    }
    return "";
  }

  function truncateText(value, maxLength) {
    const text = String(value || "").trim();
    if (!text || text.length <= maxLength) {
      return text;
    }
    return `${text.slice(0, maxLength).trimEnd()}…`;
  }

  async function buildVaultCreationPayload(masterPassword, iterations) {
    const salt = randomBytes(16);
    const saltB64 = bytesToBase64(salt);
    const key = await deriveVaultKey(masterPassword, saltB64, iterations);
    const check = await encryptJsonPayload(
      {
        marker: "DSTT_VAULT_CHECK_V1",
        createdAt: new Date().toISOString(),
      },
      key
    );
    return {
      key,
      payload: {
        kdf_name: "PBKDF2-SHA-256",
        kdf_iterations: iterations,
        salt_b64: saltB64,
        check_nonce_b64: check.nonce_b64,
        check_ciphertext_b64: check.ciphertext_b64,
      },
    };
  }

  async function deriveVaultKey(masterPassword, saltB64, iterations) {
    const importedPassword = await window.crypto.subtle.importKey(
      "raw",
      encoder.encode(masterPassword),
      "PBKDF2",
      false,
      ["deriveKey"]
    );
    return window.crypto.subtle.deriveKey(
      {
        name: "PBKDF2",
        salt: base64ToBytes(saltB64),
        iterations,
        hash: "SHA-256",
      },
      importedPassword,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"]
    );
  }

  async function encryptJsonPayload(value, key) {
    const iv = randomBytes(12);
    const plaintext = encoder.encode(JSON.stringify(value));
    const ciphertext = await window.crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext);
    return {
      nonce_b64: bytesToBase64(iv),
      ciphertext_b64: bytesToBase64(new Uint8Array(ciphertext)),
    };
  }

  async function decryptJsonPayload(value, key) {
    const plaintext = await window.crypto.subtle.decrypt(
      { name: "AES-GCM", iv: base64ToBytes(value.nonce_b64) },
      key,
      base64ToBytes(value.ciphertext_b64)
    );
    return JSON.parse(decoder.decode(plaintext));
  }

  async function apiFetch(url, options) {
    const method = options.method || "GET";
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (method !== "GET" && method !== "HEAD") {
      headers.set("Content-Type", "application/json");
      if (state.csrfToken) {
        headers.set("X-CSRF-Token", state.csrfToken);
      }
    }

    const response = await window.fetch(url, {
      method,
      headers,
      body: options.body,
      credentials: "same-origin",
    });

    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = {};
      }
    }

    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `リクエストに失敗しました (${response.status})`);
    }
    return payload;
  }

  async function copyToClipboard(value, message) {
    if (!value) {
      showFlash("コピーする内容がありません。", "warn");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      showFlash(message || "コピーしました。", "success");
      window.setTimeout(async () => {
        try {
          await navigator.clipboard.writeText("");
        } catch (_error) {
          /* no-op */
        }
      }, 20000);
    } catch (_error) {
      showFlash("クリップボードへの書き込みに失敗しました。", "error");
    }
  }

  async function generateTotpCode(secret) {
    const normalized = normalizeTotpSecret(secret);
    if (!normalized) {
      throw new Error("empty totp");
    }
    let keyPromise = state.totpKeyCache.get(normalized);
    if (!keyPromise) {
      keyPromise = window.crypto.subtle.importKey(
        "raw",
        base32ToBytes(normalized),
        { name: "HMAC", hash: "SHA-1" },
        false,
        ["sign"]
      );
      state.totpKeyCache.set(normalized, keyPromise);
    }

    const key = await keyPromise;
    const counter = Math.floor(Date.now() / 1000 / 30);
    const buffer = new ArrayBuffer(8);
    const view = new DataView(buffer);
    view.setUint32(4, counter, false);
    const signature = new Uint8Array(await window.crypto.subtle.sign("HMAC", key, buffer));
    const offset = signature[signature.length - 1] & 0x0f;
    const binary =
      ((signature[offset] & 0x7f) << 24) |
      ((signature[offset + 1] & 0xff) << 16) |
      ((signature[offset + 2] & 0xff) << 8) |
      (signature[offset + 3] & 0xff);
    return String(binary % 1000000).padStart(6, "0");
  }

  function base32ToBytes(value) {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    const cleaned = value.toUpperCase().replace(/[\s=-]/g, "");
    let bits = "";
    for (const char of cleaned) {
      const index = alphabet.indexOf(char);
      if (index < 0) {
        throw new Error("invalid base32");
      }
      bits += index.toString(2).padStart(5, "0");
    }
    const bytes = [];
    for (let offset = 0; offset + 8 <= bits.length; offset += 8) {
      bytes.push(Number.parseInt(bits.slice(offset, offset + 8), 2));
    }
    return new Uint8Array(bytes);
  }

  function normalizeTotpSecret(secret) {
    return String(secret || "")
      .trim()
      .toUpperCase()
      .replace(/[\s-]/g, "");
  }

  function splitTags(tagsText) {
    return String(tagsText || "")
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean)
      .slice(0, 24);
  }

  function statCard(label, value) {
    return `
      <div class="stat-card">
        <span class="stat-card__label">${escapeHtml(label)}</span>
        <span class="stat-card__value">${escapeHtml(value)}</span>
      </div>
    `;
  }

  function healthChip(label, value) {
    return `
      <div class="health-chip">
        <span class="stat-card__label">${escapeHtml(label)}</span>
        <span class="health-chip__value">${escapeHtml(String(value))}</span>
      </div>
    `;
  }

  function showFlash(message, variant) {
    window.clearTimeout(state.flashTimer);
    els.flashBanner.hidden = false;
    els.flashBanner.dataset.variant = variant || "success";
    els.flashBanner.textContent = message;
    state.flashTimer = window.setTimeout(() => {
      els.flashBanner.hidden = true;
      els.flashBanner.textContent = "";
      els.flashBanner.removeAttribute("data-variant");
    }, 6000);
  }

  function randomBytes(length) {
    const bytes = new Uint8Array(length);
    window.crypto.getRandomValues(bytes);
    return bytes;
  }

  function randomInt(max) {
    if (!Number.isFinite(max) || max <= 0) {
      throw new Error("乱数の上限が不正です。");
    }
    const maxUint = 0xffffffff;
    const threshold = maxUint - (maxUint % max);
    const buffer = new Uint32Array(1);
    do {
      window.crypto.getRandomValues(buffer);
    } while (buffer[0] >= threshold);
    return buffer[0] % max;
  }

  function sampleChar(charset) {
    return charset[randomInt(charset.length)];
  }

  function shuffleInPlace(values) {
    for (let index = values.length - 1; index > 0; index -= 1) {
      const swapIndex = randomInt(index + 1);
      [values[index], values[swapIndex]] = [values[swapIndex], values[index]];
    }
  }

  function uniqueCharacters(value) {
    return Array.from(new Set(Array.from(String(value || "")))).join("");
  }

  function filterAmbiguous(value) {
    return Array.from(String(value || ""))
      .filter((char) => !AMBIGUOUS_CHARS.has(char))
      .join("");
  }

  function bytesToBase64(bytes) {
    let binary = "";
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
      const chunk = bytes.subarray(offset, offset + chunkSize);
      binary += String.fromCharCode.apply(null, chunk);
    }
    return window.btoa(binary);
  }

  function base64ToBytes(value) {
    const binary = window.atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function maskSecret(secret) {
    if (!secret) {
      return "未設定";
    }
    return "•".repeat(Math.max(10, Math.min(24, secret.length)));
  }

  function clampInteger(value, min, max, fallback) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.min(max, Math.max(min, parsed));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatDate(value) {
    if (!value) {
      return "不明";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "不明";
    }
    return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`;
  }

  function daysSince(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return 9999;
    }
    return Math.floor((Date.now() - date.getTime()) / (1000 * 60 * 60 * 24));
  }
})();
