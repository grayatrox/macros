// ==UserScript==
// @name         StrayaMC Vote Autofill
// @namespace    strayamc.local
// @version      1.1
// @description  Pre-fills the Minecraft username on StrayaMC's vote pages so you only need to solve the captcha and click vote. Runs in your real Firefox, so Cloudflare Turnstile behaves normally.
// @match        https://minecraftservers.org/*
// @match        https://minecraft-serverlist.com/*
// @match        https://www.minecraft-serverlist.com/*
// @match        https://www.planetminecraft.com/*
// @match        https://www.minecraftiplist.com/*
// @match        https://craftlist.org/*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// ==/UserScript==

(function () {
    "use strict";

    // Your Minecraft username lives in Tampermonkey's storage for this script,
    // not in this file, so the file can be published without it. Asked for on
    // the first vote page; change it from the Tampermonkey menu.
    function askUsername() {
        const answer = prompt(
            "StrayaMC Vote Autofill: your Minecraft username",
            GM_getValue("username", ""),
        );
        const name = (answer || "").trim();
        if (name) GM_setValue("username", name);
        return name;
    }
    GM_registerMenuCommand("Set Minecraft username", askUsername);

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

    const USERNAME = GM_getValue("username", "") || askUsername();
    if (!USERNAME) return;

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
