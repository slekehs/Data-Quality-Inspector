"use strict";

let activeTour = null;

function startGuidedTour(trigger) {
  if (activeTour) return;
  const scrollX = window.scrollX, scrollY = window.scrollY;
  const frame = document.createElement("iframe");
  frame.id = "guided-tour-frame";
  frame.title = "Inspector tour example";
  frame.tabIndex = -1;
  frame.inert = true;
  frame.setAttribute("aria-hidden", "true");
  let index = 0, ready = false;
  const sections = {upload: "Main page", checks: "Choose checks", report: "Inspection report",
                    "single-task": "Review one task", settings: "Settings", walkthrough: "Walkthrough"};

  function finish(restore = true) {
    if (activeTour !== frame) return;
    activeTour = null;
    clearTimeout(timeout);
    window.removeEventListener("pagehide", leavePage);
    overlay.close();
    frame.remove();
    document.documentElement.classList.remove("guided-tour-active");
    if (restore) {
      if (trigger.isConnected) trigger.focus({preventScroll: true});
      window.scrollTo(scrollX, scrollY);
    }
  }
  function fail() {
    if (activeTour !== frame) return;
    finish();
    showError(new Error("The tour example could not be opened. Start the tour again to retry."));
  }
  function show(indexToShow) {
    const step = TOUR_STOPS[indexToShow];
    try {
      frame.contentWindow.showTourPage(step.page);
      const target = frame.contentDocument.querySelector(step.target);
      if (!target) throw new Error("Missing tour target");
      index = indexToShow;
      overlay.show({...step, target, section: sections[step.page], index, total: TOUR_STOPS.length});
    } catch { fail(); }
  }
  function leavePage() { finish(false); }
  const overlay = createTourOverlay({
    onBack() { if (ready && activeTour === frame && index > 0) show(index - 1); },
    onNext() {
      if (!ready || activeTour !== frame) return;
      if (index === TOUR_STOPS.length - 1) finish();
      else show(index + 1);
    },
    onSkip() { finish(); }
  });
  const timeout = setTimeout(fail, 15000);
  activeTour = frame;
  document.documentElement.classList.add("guided-tour-active");
  frame.addEventListener("error", fail, {once: true});
  frame.addEventListener("load", async () => {
    if (activeTour !== frame) return;
    try {
      await frame.contentWindow.prepareTourPreview();
      if (activeTour !== frame) return;
      clearTimeout(timeout);
      ready = true;
      show(0);
    } catch { fail(); }
  }, {once: true});
  frame.src = "/tour-preview";
  window.addEventListener("pagehide", leavePage, {once: true});
  document.body.append(frame);
  overlay.show({loading: true, target: null, title: "Opening the tour", description: "Loading the example workspace…", index: 0, total: TOUR_STOPS.length});
}

for (const id of ["start-guided-tour", "start-home-tour"]) {
  document.getElementById(id).addEventListener("click", (event) => startGuidedTour(event.currentTarget));
}

function openLinkedTour() {
  if (!state.bootstrap || state.busy) return;
  if (state.utility === "walkthrough") renderPage();
  else openUtility("walkthrough");
  startGuidedTour($("#start-guided-tour"));
}
window.addEventListener("hashchange", () => {
  if (window.location.hash === "#tour") openLinkedTour();
});
