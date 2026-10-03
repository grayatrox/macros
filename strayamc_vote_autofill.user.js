// ==UserScript==
// @name         StrayaMC Vote Autofill
// @namespace    strayamc.local
// @version      1.0
// @description  Pre-fills the Minecraft username on StrayaMC's vote pages so you only need to solve the captcha and click vote. Runs in your real Firefox, so Cloudflare Turnstile behaves normally.
// @author       YourMinecraftName
// @match        https://minecraftservers.org/*
// @match        https://minecraft-serverlist.com/*
// @match        https://www.minecraft-serverlist.com/*
// @match        https://www.planetminecraft.com/*
// @match        https://www.minecraftiplist.com/*
// @match        https://craftlist.org/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
    "use strict";

    // Your Minecraft username.
    const USERNAME = "YourMinecraftName";

    // host (without leading "www.")  ->  CSS selector for that site's username box.
    // Selectors confirmed by inspecting each live vote page.
    const SELECTORS = {
        "minecraftservers.org":      "input#username",
        "minecraft-serverlist.com":  "input[name='mc_username']",
        "planetminecraft.com":       "input[name='mcname']",
        "minecraftiplist.com":       "input[placeholder='Username']",
        "craftlist.org":             "#frm-voteForm-nickName",
    };

    const host = location.hostname.replace(/^www\./, "");
    const selector = SELECTORS[host];
    if (!selector) return;

    // React/Vue track their own value; setting .value alone is ignored, so use
    // the native setter and fire input+change so the framework registers it.
    function setValue(el, value) {
        const proto = Object.getPrototypeOf(el);
        const desc = Object.getOwnPropertyDescriptor(proto, "value");
        if (desc && desc.set) {
            desc.set.call(el, value);
        } else {
            el.value = value;
        }
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
    }

    let done = false;
    function tryFill() {
        if (done) return true;
        const el = document.querySelector(selector);
        if (!el) return false;
        // Don't clobber something you've already typed.
        if (el.value && el.value.trim() && el.value !== USERNAME) return true;
        setValue(el, USERNAME);
        done = true;
        console.log("[StrayaMC Vote Autofill] filled", selector, "on", host);
        return true;
    }

    // The forms render late (SPAs / after scripts run), so watch the DOM until
    // the field appears, then stop. Give up after 20s.
    if (tryFill()) return;
    const observer = new MutationObserver(() => {
        if (tryFill()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => observer.disconnect(), 20000);
})();
