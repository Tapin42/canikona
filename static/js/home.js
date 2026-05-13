function parsePageConfig() {
  const node = document.getElementById("page-config");
  if (!node) return { races: [] };
  try {
    return JSON.parse(node.textContent || "{}");
  } catch (_) {
    return { races: [] };
  }
}

function setupTooltip(tooltipContainer) {
  const icon = tooltipContainer.querySelector(".tooltip-icon");
  if (!icon) return;
  icon.addEventListener("click", (e) => {
    e.stopPropagation();
    document.querySelectorAll(".tooltip.active").forEach((tooltip) => {
      if (tooltip !== tooltipContainer) tooltip.classList.remove("active");
    });
    tooltipContainer.classList.toggle("active");
  });
}

function slugifyRace(name) {
  return (name || "").replace(/ /g, "_");
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".tooltip")) {
    document.querySelectorAll(".tooltip.active").forEach((tooltip) => tooltip.classList.remove("active"));
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const config = parsePageConfig();
  const racesData = config.races || [];
  const currentRaceNames = new Set(config.currentRaceNames || []);

  const raceSelect = document.getElementById("race-select");
  const raceSearch = document.getElementById("race-search");
  const raceOptions = document.getElementById("race-options");
  const raceDropdownToggle = document.getElementById("race-dropdown-toggle");
  const currentRacesPanel = document.getElementById("current-races-panel");
  const currentRaceButtons = document.getElementById("current-race-buttons");
  const currentRacesHeader = document.getElementById("current-races-header");
  const sourceRadios = Array.from(document.querySelectorAll('input[name="data_source"]'));
  const genderRadios = Array.from(document.querySelectorAll('input[name="gender"]'));
  const genderDiv = document.getElementById("gender-selection");
  const iframe = document.getElementById("results-iframe");
  const messageArea = document.getElementById("message-area");
  const loadingOverlay = document.getElementById("loading-overlay");
  const officialAgRadio = document.querySelector('input[name="data_source"][value="official_ag"]');
  const liveRadio = document.querySelector('input[name="data_source"][value="live"]');

  let hasUserInteracted = false;

  function findRaceData(raceName) {
    return racesData.find((race) => race.name === raceName);
  }

  function currentRaceOption() {
    return raceSelect.options[raceSelect.selectedIndex] || null;
  }

  function isCurrentRace(name) {
    return currentRaceNames.has(name);
  }

  function setCurrentRacesCollapsed(collapsed) {
    if (!currentRacesPanel) return;
    currentRacesPanel.classList.toggle("collapsed", collapsed);
    if (currentRacesHeader) {
      currentRacesHeader.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
  }

  function updateCurrentRaceSelectionUI(selectedName) {
    if (!currentRaceButtons) return;
    currentRaceButtons.querySelectorAll(".current-race-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-race-name") === selectedName);
    });
  }

  function setRaceSearchToSelected() {
    const option = currentRaceOption();
    if (!raceSearch || !option) return;
    raceSearch.value = option.getAttribute("data-race-label") || option.textContent.trim();
  }

  function closeRaceOptions() {
    if (!raceOptions || !raceDropdownToggle) return;
    raceOptions.classList.add("hidden");
    raceDropdownToggle.setAttribute("aria-expanded", "false");
  }

  function openRaceOptions() {
    if (!raceOptions || !raceDropdownToggle) return;
    raceOptions.classList.remove("hidden");
    raceDropdownToggle.setAttribute("aria-expanded", "true");
  }

  function selectRaceValue(value, pushHistory = true) {
    if (!value) return false;
    const next = Array.from(raceSelect.options).find((option) => option.value === value);
    if (!next) return false;
    raceSelect.value = next.value;
    updateCurrentRaceSelectionUI(next.value);
    if (isCurrentRace(next.value)) {
      raceSearch.value = "";
      setCurrentRacesCollapsed(false);
    } else {
      setRaceSearchToSelected();
      setCurrentRacesCollapsed(true);
    }
    hasUserInteracted = pushHistory;
    updateAll(pushHistory);
    return true;
  }

  function renderRaceOptions(filterText = "") {
    if (!raceOptions) return;
    const normalized = (filterText || "").trim().toLowerCase();
    const entries = Array.from(raceSelect.options).filter((option) => {
      if (isCurrentRace(option.value)) return false;
      const label = (option.getAttribute("data-race-label") || option.textContent || "").toLowerCase();
      return !normalized || label.includes(normalized);
    });
    raceOptions.innerHTML = "";

    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "race-option-empty";
      empty.textContent = "No races match your search";
      raceOptions.appendChild(empty);
      return;
    }

    entries.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "race-option-item";
      button.setAttribute("role", "option");
      button.textContent = option.getAttribute("data-race-label") || option.textContent.trim();
      button.addEventListener("click", () => {
        selectRaceValue(option.value, true);
        closeRaceOptions();
      });
      raceOptions.appendChild(button);
    });
  }

  function officialAvailable(race) {
    if (!race || !race.results_urls || !("official_ag" in race.results_urls)) return false;
    if (race.distance === "70.3") {
      const men = race.results_urls.official_ag?.men ?? "";
      const women = race.results_urls.official_ag?.women ?? "";
      return Boolean((men && men.trim()) || (women && women.trim()));
    }
    const url = race.results_urls.official_ag;
    if (typeof url === "string") return Boolean(url.trim());
    if (typeof url === "object" && url !== null) {
      const men = url.men ?? "";
      const women = url.women ?? "";
      return Boolean((men && men.trim()) || (women && women.trim()));
    }
    return false;
  }

  function officialGendered(race, source) {
    const officialAg = race.results_urls?.official_ag;
    return (
      source === "official_ag" &&
      race.distance === "140.6" &&
      typeof officialAg === "object" &&
      officialAg !== null &&
      ((officialAg.men && officialAg.men.trim()) || (officialAg.women && officialAg.women.trim()))
    );
  }

  async function refreshSlotSummary(race, gender) {
    try {
      const name = slugifyRace(race?.name || "");
      if (!name) return;
      const params = new URLSearchParams();
      if (gender) params.set("gender", gender);
      const res = await fetch(`/fragment/slot_summary/${name}?${params.toString()}`);
      if (!res.ok) return;
      const html = await res.text();
      const container = document.getElementById("slot-summary-container");
      if (!container) return;
      container.innerHTML = html;

      const scripts = Array.from(container.querySelectorAll("script"));
      scripts.forEach((oldScript) => {
        const s = document.createElement("script");
        for (const attr of oldScript.attributes) s.setAttribute(attr.name, attr.value);
        s.text = oldScript.textContent || "";
        document.body.appendChild(s);
        oldScript.remove();
      });
    } catch (_) {}
  }

  function updateRolldownInfo(source, race) {
    const rolldownDiv = document.getElementById("rolldown-info");
    const rolldownContent = document.getElementById("rolldown-content");
    if (!rolldownDiv || !rolldownContent) return;

    if (source !== "official_ag" || !race) {
      rolldownDiv.style.display = "none";
      return;
    }

    let rolldownPosition = null;
    if (race.known_rolldown) {
      if (race.distance === "70.3") {
        const selectedGender = document.querySelector('input[name="gender"]:checked')?.value;
        if (selectedGender && race.known_rolldown[selectedGender]) rolldownPosition = race.known_rolldown[selectedGender];
      } else if (race.distance === "140.6" && typeof race.known_rolldown === "number") {
        rolldownPosition = race.known_rolldown;
      }
    }

    if (rolldownPosition) {
      rolldownContent.innerHTML = `<span class="rolldown-content-text"><strong>Performance Pool Info</strong>: The performance pool rolled down to at least position <strong>${rolldownPosition}</strong>.</span>`;
    } else {
      rolldownContent.innerHTML = `<p class="rolldown-content-text"><strong>Performance Pool Info</strong>: No confirmed performance pool depth is recorded for this race yet.</p>`;
    }
    rolldownDiv.style.display = "block";
  }

  function showLoading() {
    loadingOverlay.style.display = "block";
    iframe.style.display = "none";
    messageArea.style.display = "none";
  }

  function getCurrentTheme() {
    const theme = document.documentElement.getAttribute("data-theme");
    return theme === "dark" || theme === "light" ? theme : null;
  }

  function notifyLiveIframeTheme() {
    const theme = getCurrentTheme();
    if (!theme || !iframe || !iframe.src || !iframe.src.includes("/live_results/")) return;
    if (iframe.contentWindow) {
      iframe.contentWindow.postMessage({ type: "canikona:set-theme", theme }, window.location.origin);
    }
  }

  function attachIframeLoadHandler() {
    iframe.onload = () => {
      hideLoading();
      notifyLiveIframeTheme();
    };
  }

  function applyThemeParamToLiveIframe() {
    if (!iframe || !iframe.src || !iframe.src.includes("/live_results/")) return;
    const currentTheme = getCurrentTheme();
    if (currentTheme !== "dark" && currentTheme !== "light") return;
    const iframeUrl = new URL(iframe.src, window.location.origin);
    iframeUrl.searchParams.set("theme", currentTheme);
    iframe.src = iframeUrl.pathname + iframeUrl.search;
  }

  function hideLoading() {
    loadingOverlay.style.display = "none";
  }

  function writeCanonicalQuery(race, source, gender) {
    if (!race) return;
    const params = new URLSearchParams();
    params.set("race", slugifyRace(race.name));
    params.set("source", source);
    if (gender) params.set("gender", gender);
    const next = `/?${params.toString()}`;
    if (hasUserInteracted) {
      window.history.pushState({}, "", next);
    } else {
      window.history.replaceState({}, "", next);
    }
  }

  function syncFromUrl(search) {
    const params = new URLSearchParams(search);
    const raceSlug = params.get("race");
    const source = params.get("source");
    const gender = params.get("gender");
    const race = racesData.find((r) => slugifyRace(r.name) === raceSlug);
    if (!race) return;

    raceSelect.value = race.name;
    updateCurrentRaceSelectionUI(race.name);
    if (isCurrentRace(race.name)) {
      raceSearch.value = "";
      setCurrentRacesCollapsed(false);
    } else {
      setRaceSearchToSelected();
      setCurrentRacesCollapsed(true);
    }
    if (source === "official_ag" || source === "live") {
      const sourceInput = document.querySelector(`input[name="data_source"][value="${source}"]`);
      if (sourceInput) sourceInput.checked = true;
    }
    if (gender === "men" || gender === "women") {
      const genderInput = document.querySelector(`input[name="gender"][value="${gender}"]`);
      if (genderInput) genderInput.checked = true;
    }
    updateAll(false);
  }

  function updateSourceAvailability(race) {
    const hasOfficialAg = officialAvailable(race);
    officialAgRadio.disabled = !hasOfficialAg;
    officialAgRadio.parentElement.classList.toggle("disabled-controls", !hasOfficialAg);
    liveRadio.disabled = false;
    liveRadio.parentElement.classList.remove("disabled-controls");
    if (!hasOfficialAg && officialAgRadio.checked) {
      liveRadio.checked = true;
    }
  }

  function updateGenderAvailability(race, source) {
    const slotPolicy = race.slot_policy || "";
    const isSplit = slotPolicy.startsWith("split");
    const allowGender = isSplit || officialGendered(race, source);

    if (allowGender) {
      genderDiv.classList.remove("disabled-controls");
      genderRadios.forEach((radio) => {
        radio.disabled = false;
      });
      const checked = document.querySelector('input[name="gender"]:checked');
      if (!checked) {
        const menRadio = document.querySelector('input[name="gender"][value="men"]');
        if (menRadio) menRadio.checked = true;
      }
    } else {
      genderDiv.classList.add("disabled-controls");
      genderRadios.forEach((radio) => {
        radio.disabled = true;
      });
    }
    return allowGender;
  }

  async function updateAll(pushHistory = true) {
    const selectedRaceName = raceSelect.value;
    const race = findRaceData(selectedRaceName);
    if (!race) return;
    updateSourceAvailability(race);
    const source = document.querySelector('input[name="data_source"]:checked').value;
    const allowGender = updateGenderAvailability(race, source);
    const gender = allowGender ? document.querySelector('input[name="gender"]:checked')?.value || "men" : null;

    if (pushHistory) {
      writeCanonicalQuery(race, source, gender);
    }

    showLoading();
    updateRolldownInfo(source, race);
    refreshSlotSummary(race, gender);

    if (source === "official_ag" && race.results_urls && race.results_urls.official_ag) {
      const url = race.results_urls.official_ag;
      if (race.distance === "70.3" && typeof url === "object") {
        if (url[gender] && url[gender] !== "") {
          attachIframeLoadHandler();
          iframe.src = url[gender];
          iframe.style.display = "block";
          messageArea.style.display = "none";
        } else {
          iframe.style.display = "none";
          messageArea.style.display = "block";
          messageArea.innerText = "Coming Soon!";
          hideLoading();
        }
      } else if (race.distance === "140.6") {
        if (typeof url === "object" && url !== null) {
          if (gender && url[gender] && url[gender] !== "") {
            attachIframeLoadHandler();
            iframe.src = url[gender];
            iframe.style.display = "block";
            messageArea.style.display = "none";
          } else {
            iframe.style.display = "none";
            messageArea.style.display = "block";
            messageArea.innerText = "Coming Soon!";
            hideLoading();
          }
        } else if (typeof url === "string") {
          attachIframeLoadHandler();
          iframe.src = url;
          iframe.style.display = "block";
          messageArea.style.display = "none";
        }
      }
    } else {
      const slotPolicy = race.slot_policy || "";
      const isSplit = slotPolicy.startsWith("split");
      const baseLivePath = isSplit ? `/live_results/${slugifyRace(selectedRaceName)}/${gender}` : `/live_results/${slugifyRace(selectedRaceName)}`;
      const liveUrlObj = new URL(baseLivePath, window.location.origin);
      const currentTheme = getCurrentTheme();
      if (currentTheme === "dark" || currentTheme === "light") {
        liveUrlObj.searchParams.set("theme", currentTheme);
      }
      attachIframeLoadHandler();
      iframe.src = liveUrlObj.pathname + liveUrlObj.search;
      iframe.style.display = "block";
      messageArea.style.display = "none";
    }
  }

  raceSelect.addEventListener("change", () => {
    hasUserInteracted = true;
    if (isCurrentRace(raceSelect.value)) {
      raceSearch.value = "";
      setCurrentRacesCollapsed(false);
    } else {
      setRaceSearchToSelected();
      setCurrentRacesCollapsed(true);
    }
    updateCurrentRaceSelectionUI(raceSelect.value);
    updateAll(true);
  });
  sourceRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      hasUserInteracted = true;
      updateAll(true);
    });
  });
  genderRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      hasUserInteracted = true;
      updateAll(true);
    });
  });

  if (raceSearch) {
    raceSearch.addEventListener("focus", () => {
      renderRaceOptions(raceSearch.value);
      openRaceOptions();
    });
    raceSearch.addEventListener("input", () => {
      renderRaceOptions(raceSearch.value);
      openRaceOptions();
    });
    raceSearch.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        const firstOption = raceOptions ? raceOptions.querySelector(".race-option-item") : null;
        if (firstOption) {
          firstOption.click();
        } else {
          closeRaceOptions();
        }
      } else if (event.key === "Escape") {
        closeRaceOptions();
      }
    });
  }

  if (raceDropdownToggle) {
    raceDropdownToggle.addEventListener("click", () => {
      const isOpen = raceOptions && !raceOptions.classList.contains("hidden");
      if (isOpen) {
        closeRaceOptions();
      } else {
        renderRaceOptions(raceSearch ? raceSearch.value : "");
        openRaceOptions();
      }
    });
  }

  if (currentRaceButtons) {
    currentRaceButtons.querySelectorAll(".current-race-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectRaceValue(btn.getAttribute("data-race-name"), true);
      });
    });
  }

  if (currentRacesHeader) {
    currentRacesHeader.addEventListener("click", () => {
      const isCollapsed = currentRacesPanel ? currentRacesPanel.classList.contains("collapsed") : false;
      setCurrentRacesCollapsed(!isCollapsed);
    });
    currentRacesHeader.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        const isCollapsed = currentRacesPanel ? currentRacesPanel.classList.contains("collapsed") : false;
        setCurrentRacesCollapsed(!isCollapsed);
      }
    });
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".race-combobox") && !event.target.closest("#race-options")) {
      closeRaceOptions();
    }
  });

  window.addEventListener("popstate", () => {
    syncFromUrl(window.location.search);
  });

  window.addEventListener("canikona:theme-changed", () => {
    applyThemeParamToLiveIframe();
    notifyLiveIframeTheme();
  });

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    if (!event.data || event.data.type !== "canikona:live-results-ready") return;
    notifyLiveIframeTheme();
  });

  if (raceSearch) {
    const selectedNow = currentRaceOption();
    if (selectedNow && isCurrentRace(selectedNow.value)) {
      raceSearch.value = "";
      setCurrentRacesCollapsed(false);
      updateCurrentRaceSelectionUI(selectedNow.value);
    } else {
      setRaceSearchToSelected();
      setCurrentRacesCollapsed(true);
      updateCurrentRaceSelectionUI(selectedNow ? selectedNow.value : "");
    }
  }
  if (window.location.search.includes("race=")) {
    syncFromUrl(window.location.search);
  } else {
    updateAll(false);
  }
});
