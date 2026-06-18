(function () {
  const setViewportHeight = function () {
    document.documentElement.style.setProperty("--auth-vh", `${window.innerHeight}px`);
  };

  setViewportHeight();
  window.addEventListener("resize", setViewportHeight);
  window.addEventListener("orientationchange", setViewportHeight);

  const form = document.querySelector("[data-login-form]");
  if (!form) {
    return;
  }

  const username = document.getElementById("username");
  const password = document.querySelector("[data-password-input]");
  const toggle = document.querySelector("[data-password-toggle]");
  const submit = document.querySelector("[data-submit-button]");
  const capsWarning = document.querySelector("[data-caps-warning]");

  if (username && !username.value) {
    username.focus();
  }

  if (toggle && password) {
    toggle.addEventListener("click", function () {
      const shouldShow = password.type === "password";
      password.type = shouldShow ? "text" : "password";
      toggle.setAttribute("aria-pressed", shouldShow ? "true" : "false");
      toggle.setAttribute("aria-label", shouldShow ? "パスワードを隠す" : "パスワードを表示");
      password.focus();
    });
  }

  if (password && capsWarning) {
    const updateCapsState = function (event) {
      const isOn = event.getModifierState && event.getModifierState("CapsLock");
      capsWarning.hidden = !isOn;
    };
    password.addEventListener("keydown", updateCapsState);
    password.addEventListener("keyup", updateCapsState);
    password.addEventListener("blur", function () {
      capsWarning.hidden = true;
    });
  }

  form.addEventListener("submit", function () {
    if (submit) {
      submit.classList.add("is-loading");
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
    }
  });
})();
