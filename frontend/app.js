// ==========================================================================
// TILMOCHBEK.UZ — AUTHENTIK EDITORIAL ART STUDIO CONTROLLER
// High-Fashion Brutalism | Technical Grid Indexing | Full State & SSE Management
// ==========================================================================

document.addEventListener("DOMContentLoaded", () => {
    // 1. Initialize Lucide Icons
    if (window.lucide) {
        lucide.createIcons();
    }

    // 2. Initialize Core Subsystems
    loadConfig().then(renderPlans);
    initAuthState();
    initVoiceSelectors();
    initFormSubmit();
    loadUserHistory();
});

// Global Application State
let currentTab = "studio";
let currentJobId = null;
let eventSource = null;
let currentUser = null;
let appConfig = { plans: [], demo_login: false, telegram_bot: "tilmoch_bek_bot", payment_info: "" };
let referralUrl = "";

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtMoney(n) { return Number(n).toLocaleString("ru-RU").replace(/ /g, " "); }

// --- TAB SWITCHER ([ 01 // STUDIO ] & [ 02 // TARIX ]) ---
function switchTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll(".tab-content").forEach(el => el.classList.add("hidden"));
    document.querySelectorAll(".editorial-tab").forEach(el => el.classList.remove("active"));

    const activeSection = document.getElementById(`tab-${tabName}`);
    if (activeSection) {
        activeSection.classList.remove("hidden");
    }

    const activeBtn = document.getElementById(`tabBtn-${tabName}`);
    if (activeBtn) {
        activeBtn.classList.add("active");
    }

    if (tabName === "history") {
        loadUserHistory();
    }
    if (tabName === "plans") {
        renderPlans();
    }

    if (window.lucide) {
        lucide.createIcons();
    }
}

// --- CLIPBOARD PASTE INTERFACE ---
async function handlePasteUrl() {
    const input = document.getElementById("youtubeUrl");
    try {
        if (navigator.clipboard && navigator.clipboard.readText) {
            const text = await navigator.clipboard.readText();
            if (text && (text.includes("youtube.com") || text.includes("youtu.be"))) {
                input.value = text.trim();
                input.focus();
                return;
            }
        }
    } catch (e) {
        console.log("Clipboard API read info:", e);
    }
    input.focus();
}

// --- VOICE SELECTOR MODULES (01 SARDOR / 02 MADINA) ---
function initVoiceSelectors() {
    const voiceCards = document.querySelectorAll(".voice-card");
    voiceCards.forEach(card => {
        card.addEventListener("click", () => {
            voiceCards.forEach(c => {
                c.classList.remove("active");
                const radio = c.querySelector('input[type="radio"]');
                if (radio) radio.checked = false;
            });
            card.classList.add("active");
            const radio = card.querySelector('input[type="radio"]');
            if (radio) radio.checked = true;
        });
    });
}

// --- AUTHENTICATION & SESSION MANAGEMENT ---
function getAuthHeader() {
    const token = localStorage.getItem("tilmochbek_jwt") || localStorage.getItem("gapirai_jwt");
    return token ? { "Authorization": `Bearer ${token}` } : {};
}

async function initAuthState() {
    try {
        const res = await fetch("/api/user/me", { headers: getAuthHeader() });
        const data = await res.json();
        
        if (data.authenticated && data.user) {
            currentUser = data.user;
            renderLoggedInUI(data.user);
            renderUsage(data.usage);
            referralUrl = data.referral_url || "";
            const box = document.getElementById("referralBox");
            if (box && referralUrl) {
                box.classList.remove("hidden");
                document.getElementById("referralLink").textContent = referralUrl;
            }
        } else {
            currentUser = null;
            renderLoggedOutUI();
        }
    } catch (e) {
        console.warn("User auth status check:", e);
        renderLoggedOutUI();
    }
}

function renderLoggedInUI(user) {
    document.getElementById("loginBtn").classList.add("hidden");
    const userMenu = document.getElementById("userMenuContainer");
    userMenu.classList.remove("hidden");
    document.getElementById("userNameHeader").textContent = user.full_name.split(" ")[0];
    document.getElementById("dropdownUserName").textContent = user.full_name;
    if (user.avatar_url) {
        document.getElementById("userAvatarImg").src = user.avatar_url;
    }
}

function renderLoggedOutUI() {
    document.getElementById("loginBtn").classList.remove("hidden");
    document.getElementById("userMenuContainer").classList.add("hidden");
    const strip = document.getElementById("usageStrip");
    if (strip) {
        strip.classList.remove("hidden");
        strip.innerHTML = 'Dublyaj qilish uchun <a onclick="openAuthModal()">tizimga kiring</a>. <b>Birinchi video bepul!</b>';
    }
}

function renderUsage(u) {
    const strip = document.getElementById("usageStrip");
    const planLabel = document.getElementById("dropdownPlan");
    if (!strip || !u) return;
    if (planLabel) planLabel.textContent = `// ${u.plan_name.toUpperCase()}`;
    const pct = u.videos_total ? Math.min(100, Math.round(u.videos_used / u.videos_total * 100)) : 0;
    const when = u.expires_at ? ` · ${u.expires_at.slice(0, 10)} gacha` : (u.resets_at ? ` · yangilanishi: ${u.resets_at.slice(0, 10)}` : "");
    strip.classList.remove("hidden");
    strip.innerHTML = `Tarif: <b>${escapeHtml(u.plan_name)}</b>${when}
        <span class="tb-meter"><span style="width:${pct}%"></span></span>
        Qolgan videolar: <b>${u.videos_remaining}</b>
        ${u.videos_remaining <= 0 ? '<a onclick="switchTab(\'plans\')">Tarifni oshirish</a>' : ''}`;
}

async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        appConfig = await res.json();
        const demo = document.getElementById("demoLoginBtn");
        if (demo && appConfig.demo_login) demo.classList.remove("hidden");
        const missing = document.getElementById("googleMissing");
        if (missing && !appConfig.google_client_id) missing.classList.remove("hidden");
        const bot = appConfig.telegram_bot || "tilmoch_bek_bot";
        ["openTelegramBotLink", "payTelegramLink"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.href = `https://t.me/${bot}`;
        });
        const pay = document.getElementById("paymentInfo");
        if (pay) pay.textContent = appConfig.payment_info || "";
    } catch (e) { console.warn("config:", e); }
}

function renderPlans() {
    const grid = document.getElementById("plansGrid");
    if (!grid || !appConfig.plans) return;
    grid.innerHTML = appConfig.plans.map(p => `
        <div class="tb-plan ${p.key === "monthly" ? "featured" : ""}">
            ${p.key === "monthly" ? '<span class="tb-badge">OMMABOP</span>' : ""}
            <h3>${escapeHtml(p.name)}</h3>
            ${p.old_price ? `<div class="tb-old">${fmtMoney(p.old_price)} so'm</div>` : ""}
            <div class="tb-price">${p.price ? fmtMoney(p.price) : "Bepul"} <small>${p.price ? "so'm" : ""}</small></div>
            <ul>
                <li>${p.videos} ta video / ${p.days} kun</li>
                <li>Har biri ${p.max_minutes} daqiqagacha</li>
                <li>Erkak va ayol ovozi</li>
                <li>Veb va Telegram bot</li>
            </ul>
            ${p.price ? `<button class="editorial-cta-btn py-2.5 px-4 text-xs" onclick="buyPlan('${p.key}')">SOTIB OLISH</button>` : ""}
        </div>`).join("");
}

async function buyPlan(planKey) {
    if (!currentUser) { openAuthModal(); return; }
    if (!appConfig.click_enabled) {
        alert((appConfig.payment_info || "To'lov uchun Telegram botga yozing.") + "\n\nTo'lovdan so'ng chekni Telegram botga yuboring.");
        return;
    }
    try {
        const res = await fetch("/api/payments/click", {
            method: "POST", headers: { "Content-Type": "application/json", ...getAuthHeader() },
            body: JSON.stringify({ plan: planKey })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Xatolik");
        window.location.href = data.url;
    } catch (e) { alert("To'lovni boshlab bo'lmadi: " + e.message); }
}

function copyReferral() {
    if (!referralUrl) return;
    navigator.clipboard?.writeText(referralUrl).then(() => alert("Havola nusxalandi!"));
}

function toggleUserMenu() {
    const dropdown = document.getElementById("userDropdown");
    dropdown.classList.toggle("hidden");
}

function closeUserMenu() {
    const dropdown = document.getElementById("userDropdown");
    if (dropdown) dropdown.classList.add("hidden");
}

function handleLogout() {
    localStorage.removeItem("tilmochbek_jwt");
    localStorage.removeItem("gapirai_jwt");
    currentUser = null;
    renderLoggedOutUI();
    loadUserHistory();
    switchTab("studio");
}

function openAuthModal() {
    document.getElementById("authModal").classList.remove("hidden");
    initGoogleGSI();
    if (window.lucide) lucide.createIcons();
}

function closeAuthModal() {
    document.getElementById("authModal").classList.add("hidden");
}

async function handleDemoLogin() {
    try {
        const res = await fetch("/api/auth/demo-login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: "Foydalanuvchi" })
        });
        const data = await res.json();
        if (data.access_token) {
            localStorage.setItem("tilmochbek_jwt", data.access_token);
            currentUser = data.user;
            closeAuthModal();
            initAuthState();
            loadUserHistory();
        }
    } catch (e) {
        alert("Kirishda xatolik: " + e.message);
    }
}

async function initGoogleGSI() {
    const container = document.getElementById("gsiContainer");
    if (!container) return;

    try {
        let clientId = "";
        try {
            const configRes = await fetch("/api/config");
            const configData = await configRes.json();
            if (configData.google_client_id) {
                clientId = configData.google_client_id;
            }
        } catch (e) {
            console.warn("Config fetch info:", e);
        }

        if (clientId && window.google && window.google.accounts) {
            google.accounts.id.initialize({
                client_id: clientId,
                callback: handleGoogleResponse
            });
            google.accounts.id.renderButton(container, {
                theme: "filled_black",
                size: "large",
                shape: "rectangular",
                text: "signin_with"
            });
        }
    } catch (e) {
        console.log("Google GSI info:", e);
    }
}

async function handleGoogleResponse(response) {
    if (!response || !response.credential) return;
    try {
        const res = await fetch("/api/auth/google", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ credential: response.credential })
        });
        const data = await res.json();
        if (data.access_token) {
            localStorage.setItem("tilmochbek_jwt", data.access_token);
            currentUser = data.user;
            closeAuthModal();
            initAuthState();
            loadUserHistory();
        }
    } catch (e) {
        alert("Google orqali kirishda xatolik: " + e.message);
    }
}

function openTelegramModal() {
    document.getElementById("telegramModal").classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
}

function closeTelegramModal() {
    document.getElementById("telegramModal").classList.add("hidden");
}

// --- USER HISTORY (ARCHIVE CATALOG) ---
async function loadUserHistory() {
    const grid = document.getElementById("historyGrid");
    const empty = document.getElementById("emptyHistory");

    try {
        const endpoint = "/api/user/history";
        const res = await fetch(endpoint, { headers: getAuthHeader() });
        if (!res.ok) {
            console.warn("History fetch status:", res.status);
            return;
        }
        const data = await res.json();
        const history = data.history || [];

        const badge = document.getElementById("historyCountBadge");
        if (badge) {
            badge.textContent = history.length;
            badge.classList.remove("hidden");
        }

        if (!grid) return;

        if (history.length === 0) {
            grid.innerHTML = "";
            if (empty) empty.classList.remove("hidden");
            return;
        }

        if (empty) empty.classList.add("hidden");
        grid.innerHTML = history.map((item, idx) => `
            <div class="history-card corner-crosshairs p-5 space-y-4 flex flex-col justify-between ${item.expired ? 'tb-expired' : ''}">
                <div class="corner-crosshairs-bottom"></div>
                
                <div class="space-y-2.5">
                    <div class="flex items-center justify-between text-[10px] font-mono-tech text-zinc-400">
                        <span class="px-2 py-0.5 bg-white/10 text-white font-bold tracking-wider">
                            ARCHIVE_${String(idx + 1).padStart(2, '0')}
                        </span>
                        <span class="text-zinc-500">${item.created_at ? new Date(item.created_at).toLocaleDateString() : ''}</span>
                    </div>

                    <h4 class="font-display font-bold text-base text-white line-clamp-2 leading-snug" title="${escapeHtml(item.video_title)}">
                        ${escapeHtml(item.video_title)}
                    </h4>

                    <div class="flex items-center gap-3 text-[11px] font-mono-tech text-zinc-400">
                        <span class="flex items-center gap-1">
                            <i data-lucide="clock" class="w-3 h-3 text-[#D4FF00]"></i>
                            <span>${item.video_duration}s</span>
                        </span>
                        <span class="text-zinc-600">//</span>
                        <span class="text-zinc-300">${item.voice_gender === 'female' ? '02 // MADINA' : '01 // SARDOR'}</span>
                    </div>
                </div>

                <div class="pt-3 border-t border-white/10 flex items-center justify-between gap-2">
                    ${item.expired || !item.video_url ? '<span class="flex-1 text-[11px] font-mono-tech text-zinc-500">Fayl muddati tugagan (24 soat)</span>' : `
                    <button
                        data-url="${escapeHtml(item.video_url)}" data-title="${escapeHtml(item.video_title)}"
                        onclick="playHistoryVideo(this.dataset.url, this.dataset.title)"
                        class="flex-1 py-2 px-3 editorial-outline-btn flex items-center justify-center gap-1.5 hover:bg-[#D4FF00] hover:text-black hover:border-[#D4FF00] transition-all"
                    >
                        <i data-lucide="play" class="w-3 h-3"></i>
                        <span>[ IJRO ETISH ]</span>
                    </button>
                    <a
                        href="${escapeHtml(item.video_url)}"
                        download
                        class="p-2 editorial-outline-btn hover:bg-white hover:text-black"
                        title="Yuklab olish"
                    >
                        <i data-lucide="download" class="w-3.5 h-3.5"></i>
                    </a>`}
                </div>
            </div>
        `).join("");

        if (window.lucide) lucide.createIcons();
    } catch (e) {
        console.error("History fetch error:", e);
    }
}

function playHistoryVideo(videoUrl, title) {
    if (!videoUrl) return;
    switchTab("studio");
    const resultCard = document.getElementById("resultCard");
    const videoPlayer = document.getElementById("videoPlayer");
    const videoSource = document.getElementById("videoSource");
    const videoTitle = document.getElementById("videoTitleDisplay");
    const downloadBtn = document.getElementById("downloadBtn");

    videoTitle.textContent = title;
    videoSource.src = videoUrl;
    downloadBtn.href = videoUrl;
    videoPlayer.load();

    resultCard.classList.remove("hidden");
    resultCard.scrollIntoView({ behavior: "smooth" });
}

// --- DUBBING PIPELINE SUBMISSION ---
function initFormSubmit() {
    const form = document.getElementById("dubForm");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const urlInput = document.getElementById("youtubeUrl");
        const url = urlInput.value.trim();
        if (!url) {
            alert("Iltimos, YouTube video havolasini kiriting!");
            return;
        }

        const voiceGender = document.querySelector('input[name="voice_gender"]:checked')?.value || "male";
        startYouTubeDubbing(url, voiceGender);
    });
}

async function startYouTubeDubbing(url, voiceGender) {
    const submitBtn = document.getElementById("submitBtn");
    submitBtn.disabled = true;

    showProgressCard();

    try {
        const res = await fetch("/api/dub", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...getAuthHeader()
            },
            body: JSON.stringify({ url, voice_gender: voiceGender })
        });

        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            if (res.status === 401) { hideProgressCard(); submitBtn.disabled = false; openAuthModal(); return; }
            if (res.status === 402) { hideProgressCard(); submitBtn.disabled = false; alert(errData.detail || "Limit tugadi."); switchTab("plans"); return; }
            throw new Error(typeof errData.detail === "string" ? errData.detail : "So'rovda xatolik");
        }

        const data = await res.json();
        currentJobId = data.job_id;

        if (data.status === "completed") {
            fetchJobDetails(currentJobId);
        } else {
            listenToProgressSSE(currentJobId);
        }
    } catch (err) {
        alert("Xatolik: " + err.message);
        hideProgressCard();
        submitBtn.disabled = false;
    }
}

function showProgressCard() {
    document.getElementById("progressCard").classList.remove("hidden");
    document.getElementById("resultCard").classList.add("hidden");
    document.getElementById("progressCard").scrollIntoView({ behavior: "smooth" });
    updateProgressUI({ progress: 5, step: "YouTube Yuklab Olish", message: "Neyron Tilmoch studiyasi ishga tushmoqda..." });
}

function hideProgressCard() {
    document.getElementById("progressCard").classList.add("hidden");
}

function listenToProgressSSE(jobId) {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource(`/api/progress/${jobId}?sse=true`);

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateProgressUI(data);

            if (data.status === "completed") {
                eventSource.close();
                showCompletedResult(data);
            } else if (data.status === "failed") {
                eventSource.close();
                handleJobError(data.error || data.message);
            }
        } catch (e) {
            console.error("SSE parsing error:", e);
        }
    };

    eventSource.onerror = () => {
        eventSource.close();
        pollProgressFallback(jobId);
    };
}

async function pollProgressFallback(jobId) {
    const interval = setInterval(async () => {
        try {
            const res = await fetch(`/api/progress/${jobId}`);
            if (!res.ok) return;
            const data = await res.json();
            updateProgressUI(data);

            if (data.status === "completed") {
                clearInterval(interval);
                showCompletedResult(data);
            } else if (data.status === "failed") {
                clearInterval(interval);
                handleJobError(data.error || data.message);
            }
        } catch (e) {
            console.warn("Polling error:", e);
        }
    }, 1200);
}

async function fetchJobDetails(jobId) {
    try {
        const res = await fetch(`/api/progress/${jobId}`);
        const data = await res.json();
        if (data.status === "expired") { handleJobError(data.message || "Fayl muddati tugagan."); return; }
        updateProgressUI(data);
        showCompletedResult(data);
    } catch (e) {
        console.error("Details fetch error:", e);
    }
}

function updateProgressUI(data) {
    const pct = data.progress || 0;
    document.getElementById("progressBar").style.width = `${pct}%`;
    document.getElementById("progressPercentage").textContent = `${pct}%`;
    document.getElementById("progressStepTitle").textContent = data.message || "Bajarilmoqda...";

    // 5 Pipeline Stage Nodes
    const stepDl = document.getElementById("step-dl");
    const stepStt = document.getElementById("step-stt");
    const stepTrans = document.getElementById("step-trans");
    const stepTts = document.getElementById("step-tts");
    const stepMix = document.getElementById("step-mix");

    resetStepBadge(stepDl, pct >= 10, pct >= 30);
    resetStepBadge(stepStt, pct >= 30, pct >= 50);
    resetStepBadge(stepTrans, pct >= 50, pct >= 70);
    resetStepBadge(stepTts, pct >= 70, pct >= 90);
    resetStepBadge(stepMix, pct >= 90, pct >= 100);
}

function resetStepBadge(el, isActive, isDone) {
    if (!el) return;
    el.classList.remove("active", "completed");
    if (isDone) {
        el.classList.add("completed");
    } else if (isActive) {
        el.classList.add("active");
    }
}

function showCompletedResult(data) {
    initAuthState(); // refresh remaining-videos counter
    hideProgressCard();
    const resultCard = document.getElementById("resultCard");
    const submitBtn = document.getElementById("submitBtn");

    submitBtn.disabled = false;

    document.getElementById("videoTitleDisplay").textContent = data.title || "YouTube Dubbed Video";
    document.getElementById("videoSource").src = data.video_url;
    document.getElementById("downloadBtn").href = data.video_url;
    
    const videoPlayer = document.getElementById("videoPlayer");
    videoPlayer.load();

    // Render Subtitle Segments with Editorial Brutalist Styling
    const segmentsContainer = document.getElementById("segmentsContainer");
    const segments = data.segments || [];
    if (segments.length > 0) {
        segmentsContainer.innerHTML = segments.map((seg, i) => `
            <div class="segment-card p-3 space-y-1.5 border border-white/10">
                <div class="flex items-center justify-between text-[11px] text-zinc-400 font-mono-tech">
                    <span class="text-[#D4FF00] font-bold">SEG_${String(i+1).padStart(2, '0')} // [${Number(seg.start) || 0}s ➔ ${Number(seg.end) || 0}s]</span>
                    <span class="text-zinc-500">${seg.speech_duration ? seg.speech_duration + 's' : ''}</span>
                </div>
                <div class="text-white font-medium text-xs leading-relaxed">${escapeHtml(seg.translated_text || seg.text)}</div>
                ${seg.text && seg.text !== seg.translated_text ? `<div class="text-zinc-500 text-[11px] italic">[ASL]: ${escapeHtml(seg.text)}</div>` : ''}
            </div>
        `).join("");
    } else {
        segmentsContainer.innerHTML = `<div class="text-xs font-mono-tech text-zinc-500 italic p-2">// SUBTITR_MA'LUMOTLARI_MAVJUD_EMAS</div>`;
    }

    resultCard.classList.remove("hidden");
    resultCard.scrollIntoView({ behavior: "smooth" });
    if (window.lucide) lucide.createIcons();

    // Refresh history
    loadUserHistory();
}

function handleJobError(errorMessage) {
    hideProgressCard();
    const submitBtn = document.getElementById("submitBtn");
    submitBtn.disabled = false;
    alert("Ovozlashtirishda xatolik: " + errorMessage);
}

function copyVideoLink() {
    const videoSource = document.getElementById("videoSource");
    if (!videoSource || !videoSource.src) return;
    
    const fullUrl = videoSource.src.startsWith("http") ? videoSource.src : window.location.origin + videoSource.src;
    navigator.clipboard.writeText(fullUrl).then(() => {
        const btn = document.getElementById("copyLinkBtn");
        const origHtml = btn.innerHTML;
        btn.innerHTML = `<i data-lucide="check" class="w-3.5 h-3.5 text-[#D4FF00]"></i><span>[ NUSXALANDI! ]</span>`;
        if (window.lucide) lucide.createIcons();
        setTimeout(() => {
            btn.innerHTML = origHtml;
            if (window.lucide) lucide.createIcons();
        }, 2000);
    }).catch(err => {
        alert("Havola: " + fullUrl);
    });
}
