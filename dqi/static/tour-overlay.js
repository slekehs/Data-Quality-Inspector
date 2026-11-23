"use strict";

function createTourOverlay({onNext, onBack, onSkip}) {
  function node(tag, className, text = "") {
    const item = document.createElement(tag);
    item.className = className;
    item.textContent = text;
    return item;
  }

  const dialog = node("dialog", "tour-overlay");
  const spotlight = node("div", "tour-spotlight");
  spotlight.setAttribute("aria-hidden", "true");
  const card = node("section", "tour-card");
  const narrative = node("div", "tour-narrative");
  narrative.setAttribute("aria-live", "polite");
  narrative.setAttribute("aria-atomic", "true");
  const progress = node("p", "tour-progress");
  const heading = node("h2", "tour-heading");
  const explanation = node("p", "tour-description");
  heading.id = "tour-active-heading";
  explanation.id = "tour-active-description";
  dialog.setAttribute("aria-labelledby", heading.id);
  dialog.setAttribute("aria-describedby", explanation.id);
  narrative.append(progress, heading, explanation);

  const actions = node("div", "tour-actions");
  const skip = node("button", "button quiet tour-skip", "Skip tour");
  const back = node("button", "button secondary tour-back", "Back");
  const next = node("button", "button primary tour-next", "Next");
  for (const button of [skip, back, next]) button.type = "button";
  next.autofocus = true;
  skip.setAttribute("aria-keyshortcuts", "Escape");
  back.setAttribute("aria-keyshortcuts", "ArrowLeft");
  next.setAttribute("aria-keyshortcuts", "ArrowRight");
  skip.addEventListener("click", onSkip);
  back.addEventListener("click", onBack);
  next.addEventListener("click", onNext);
  actions.append(skip, back, next);
  card.append(narrative, actions);
  dialog.append(spotlight, card);

  let target = null;
  let observing = [];
  let frameRequest = 0;
  const resize = new ResizeObserver(scheduleLayout);
  const margin = 12, gap = 14, padding = 5;
  const clamp = (value, min, max) => Math.max(min, Math.min(value, max));

  // Map the target's visible portion into the top-level viewport. Each iframe
  // clips its contents before its own border and CSS transform are applied.
  function targetRect() {
    if (!target?.isConnected || !target.getClientRects().length) return null;
    let {left, top, right, bottom} = target.getBoundingClientRect();
    let owner = target.ownerDocument.defaultView;
    while (owner && owner !== window) {
      const frame = owner.frameElement;
      if (!frame || !frame.clientWidth || !frame.clientHeight) return null;
      const bounds = frame.getBoundingClientRect();
      const scaleX = bounds.width / frame.offsetWidth, scaleY = bounds.height / frame.offsetHeight;
      const x = bounds.left + frame.clientLeft * scaleX, y = bounds.top + frame.clientTop * scaleY;
      left = x + Math.max(0, left) * scaleX;
      right = x + Math.min(frame.clientWidth, right) * scaleX;
      top = y + Math.max(0, top) * scaleY;
      bottom = y + Math.min(frame.clientHeight, bottom) * scaleY;
      owner = frame.ownerDocument.defaultView;
    }
    return right > left && bottom > top ? {left, top, right, bottom} : null;
  }

  function layout() {
    if (!dialog.open) return;
    const viewport = window.visualViewport;
    const x = viewport?.offsetLeft ?? 0, y = viewport?.offsetTop ?? 0;
    const width = viewport?.width ?? dialog.clientWidth, height = viewport?.height ?? dialog.clientHeight;
    // Bounds also follow the visual viewport during mobile pinch zoom.
    card.style.maxWidth = Math.max(1, width - margin * 2) + "px";
    card.style.maxHeight = Math.max(1, height - margin * 2) + "px";
    const box = card.getBoundingClientRect();
    const xMin = x + margin, xMax = x + width - margin - box.width;
    const yMin = y + margin, yMax = y + height - margin - box.height;
    let cardX = (xMin + xMax) / 2, cardY = (yMin + yMax) / 2;
    const rect = targetRect();
    if (rect) {
      rect.left = Math.max(x + 2, rect.left - padding);
      rect.right = Math.min(x + width - 2, rect.right + padding);
      rect.top = Math.max(y + 2, rect.top - padding);
      rect.bottom = Math.min(y + height - 2, rect.bottom + padding);
    }

    if (rect && rect.left < rect.right && rect.top < rect.bottom) {
      cardX = clamp((rect.left + rect.right - box.width) / 2, xMin, xMax);
      cardY = clamp((rect.top + rect.bottom - box.height) / 2, yMin, yMax);
      if (rect.bottom + gap <= yMax) cardY = rect.bottom + gap;
      else if (rect.top - gap - box.height >= yMin) cardY = rect.top - gap - box.height;
      else if (rect.right + gap <= xMax) cardX = rect.right + gap;
      else if (rect.left - gap - box.width >= xMin) cardX = rect.left - gap - box.width;
      else {
        // On small screens, keep the start of a tall section in view when it
        // fits above the card. Otherwise keep the larger visible portion.
        const above = yMax - gap - rect.top;
        const below = rect.bottom - (yMin + box.height + gap);
        if (above >= below || (rect.top < y + height / 4 && above >= Math.min(96, height / 4))) {
          cardY = yMax;
          rect.bottom = Math.min(rect.bottom, cardY - gap);
        } else {
          cardY = yMin;
          rect.top = Math.max(rect.top, cardY + box.height + gap);
        }
      }
    }
    const visible = Boolean(rect && rect.right > rect.left && rect.bottom > rect.top);
    dialog.classList.toggle("tour-has-target", visible);
    spotlight.hidden = !visible;
    if (visible) {
      spotlight.style.left = rect.left + "px";
      spotlight.style.top = rect.top + "px";
      spotlight.style.width = (rect.right - rect.left) + "px";
      spotlight.style.height = (rect.bottom - rect.top) + "px";
    }
    card.style.left = cardX + "px";
    card.style.top = cardY + "px";
  }

  function scheduleLayout() {
    if (!dialog.open || frameRequest) return;
    frameRequest = requestAnimationFrame(() => { frameRequest = 0; layout(); });
  }

  function stopObserving() {
    resize.disconnect();
    observing.forEach((owner) => {
      owner.removeEventListener("scroll", scheduleLayout, true);
      owner.removeEventListener("resize", scheduleLayout);
    });
    observing = [];
    window.visualViewport?.removeEventListener("resize", scheduleLayout);
    window.visualViewport?.removeEventListener("scroll", scheduleLayout);
    if (frameRequest) cancelAnimationFrame(frameRequest);
    frameRequest = 0;
  }

  function show({target: activeTarget, title, description, index, total, section, loading = false}) {
    stopObserving();
    target = activeTarget;
    progress.textContent = loading ? "Guided tour" : [section, `Step ${index + 1} of ${total}`].filter(Boolean).join(" · ");
    heading.textContent = title;
    explanation.textContent = description;
    back.disabled = index <= 0;
    const loaded = next.disabled && !loading;
    next.disabled = loading;
    next.textContent = index === total - 1 ? "Finish tour" : "Next";
    if (!dialog.isConnected) document.body.append(dialog);
    if (!dialog.open) dialog.showModal();
    narrative.scrollTop = 0;
    const available = (window.visualViewport?.height ?? dialog.clientHeight) - card.offsetHeight - gap - margin * 2;
    if (target?.isConnected) {
      const block = target.getBoundingClientRect().height > available ? "start" : "center";
      target.scrollIntoView({block, inline: "nearest", behavior: "instant"});
      resize.observe(target);
    }
    resize.observe(card);
    for (let owner = target?.ownerDocument.defaultView ?? window; owner; owner = owner.frameElement?.ownerDocument.defaultView) {
      observing.push(owner);
      owner.addEventListener("scroll", scheduleLayout, {capture: true, passive: true});
      owner.addEventListener("resize", scheduleLayout);
      if (owner === window) break;
    }
    window.visualViewport?.addEventListener("resize", scheduleLayout);
    window.visualViewport?.addEventListener("scroll", scheduleLayout);
    layout();
    if (loaded || !dialog.contains(document.activeElement) || document.activeElement === back && back.disabled) {
      (loading ? skip : next).focus({preventScroll: true});
    }
  }

  function close() {
    stopObserving();
    if (dialog.open) dialog.close();
    dialog.remove();
    target = null;
  }

  dialog.addEventListener("cancel", (event) => { event.preventDefault(); onSkip(); });
  dialog.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      if (!event.repeat) {
        if (event.key === "ArrowRight") onNext();
        else if (!back.disabled) onBack();
      }
    } else if (event.key === "Tab") {
      if (event.shiftKey && document.activeElement === skip) {
        event.preventDefault(); next.focus();
      } else if (!event.shiftKey && document.activeElement === next) {
        event.preventDefault(); skip.focus();
      }
    }
  });
  return {show, close};
}
