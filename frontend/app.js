const form = document.querySelector("#search-form");
const queryInput = document.querySelector("#query");
const prefixInput = document.querySelector("#prefix");
const subjectOptionsElement = document.querySelector("#subject-options");
const rankInput = document.querySelector("#rank");
const studentMajorInput = document.querySelector("#student-major");
const majorOptionsElement = document.querySelector("#major-options");
const advancedFiltersElement = document.querySelector("#advanced-filters");
const filterSummaryElements = new Map(
  Array.from(document.querySelectorAll("[data-filter-summary]")).map((element) => [
    element.dataset.filterSummary,
    element,
  ]),
);
const sectionRefreshStatus = document.querySelector("#section-refresh-status");
const activeFiltersElement = document.querySelector("#active-filters");
const resultsElement = document.querySelector("#results");
const statusElement = document.querySelector("#status");
const openSectionsOnlyInput = document.querySelector("#open-sections-only");
const showMoreButton = document.querySelector("#show-more");

const PAGE_SIZE = 25;
const SEARCH_RETURN_STORAGE_KEY = "crs:return-to-search";
const CHECKBOX_FILTERS = ["location", "instruction_type", "core", "graduation_requirement", "degree_level"];
const DEFAULT_TERMS = ["fall"];
const DEFAULT_LOCATIONS = ["College Station"];
const FALLBACK_SUBJECTS = [
  { code: "BIOL", name: "Biology" },
  { code: "CHEM", name: "Chemistry" },
  { code: "CSCE", name: "Computer Science and Engineering" },
  { code: "ENGL", name: "English" },
  { code: "MATH", name: "Mathematics" },
  { code: "PHYS", name: "Physics" },
  { code: "POLS", name: "Political Science" },
];
const FALLBACK_MAJORS = ["Accounting", "Computer Science", "Finance", "Mechanical Engineering", "Marketing"];
const TERM_CODE_LABELS = {
  1: "Spring",
  2: "Summer",
  3: "Fall",
};

const ATTRIBUTE_LABELS = {
  "Core Communication (KCOM)": "Communication (KCOM)",
  "Core Mathematics (KMTH)": "Mathematics (KMTH)",
  "Core Life/Physical Sci (KLPS)": "Life & Physical Sciences (KLPS)",
  "Core Lang, Phil, Culture(KLPC)": "Language, Philosophy & Culture (KLPC)",
  "Core Lang, Phil, Culture (KLPC)": "Language, Philosophy & Culture (KLPC)",
  "Core Creative Arts (KCRA)": "Creative Arts (KCRA)",
  "Core American History (KHIS)": "American History (KHIS)",
  "Core Local Gov/Pol Sci (KPLL)": "Local Government / Political Science (KPLL)",
  "Core Fed Gov/Pol Sci (KPLF)": "Federal Government / Political Science (KPLF)",
  "Core Social & Beh Sci (KSOC)": "Social & Behavioral Sciences (KSOC)",
  "Univ Req-Writing Intensive": "Writing Intensive",
  "Univ Req-Int'l&Cult Div (KICD)": "International & Cultural Diversity (KICD)",
  "Univ Req-Cult Discourse (KUCD)": "Cultural Discourse (KUCD)",
  "Univ Req-Oral Communication": "Oral Communication",
};

const METRIC_FILTER_LABELS = {
  term: "term",
  location: "location",
  instruction_type: "instruction format",
  core: "core curriculum",
  graduation_requirement: "graduation requirement",
  student_major: "student major",
};

let currentOffset = 0;
let currentTotal = 0;
let loadedResults = [];
let activeParams = new URLSearchParams();
let subjectOptions = FALLBACK_SUBJECTS;
let majorOptions = FALLBACK_MAJORS;
let subjectTrie = createSubjectTrie(subjectOptions);
let activeSubjectIndex = -1;
let activeMajorIndex = -1;
const customSelectStates = new Map();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function shortTermLabel(termCode) {
  const value = String(termCode || "").trim();
  if (value.length < 5) {
    return "";
  }
  const termName = TERM_CODE_LABELS[value[4]];
  if (!termName) {
    return "";
  }
  return `${termName} '${value.slice(2, 4)}`;
}

function termCodeFromSectionsPath(path) {
  const match = String(path || "").match(/tamu_public_class_sections_(\d{6})/);
  return match ? match[1] : "";
}

function selectedOfferingLabel() {
  const labels = selectedTermLabels();
  return labels.length > 0 ? labels.join(" + ") : "Total Course Catalog";
}

function updateOfferingTermLabels(termCodes) {
  const latestByTerm = {};
  termCodes
    .filter(Boolean)
    .sort()
    .forEach((termCode) => {
      const termName = TERM_CODE_LABELS[String(termCode)[4]];
      if (termName) {
        latestByTerm[termName.toLowerCase()] = termCode;
      }
    });

  form.querySelectorAll('select[name="term"] option').forEach((option) => {
    const label = shortTermLabel(latestByTerm[option.value]);
    if (!label) {
      return;
    }
    option.dataset.chipLabel = label;
    option.textContent = label;
  });
  syncCustomSelect(form.querySelector('select[name="term"]'));
}

async function loadOfferingTermLabels() {
  try {
    const response = await fetch("/health");
    if (!response.ok) {
      throw new Error(`Health failed with status ${response.status}`);
    }
    const payload = await response.json();
    const termCode = payload.grade_metrics_context?.current_term_code || "";
    const termCodes = [
      ...(payload.current_sections_files || []).map(termCodeFromSectionsPath),
      termCode,
    ];
    updateOfferingTermLabels(termCodes);
  } catch {}
}

function minutesSinceRefresh(timestamp) {
  const value = new Date(timestamp);
  if (Number.isNaN(value.getTime())) {
    return null;
  }
  return Math.max(0, Math.floor((Date.now() - value.getTime()) / 60000));
}

async function loadSectionRefreshStatus() {
  if (!sectionRefreshStatus) {
    return;
  }
  try {
    const response = await fetch("/section-refresh-status");
    if (!response.ok) {
      throw new Error(`Refresh status failed with status ${response.status}`);
    }
    const payload = await response.json();
    const hourly = payload.last_hour || {};
    const number = new Intl.NumberFormat();
    const availabilityChanges = number.format(hourly.seat_status_changed_sections || 0);
    const elapsedMinutes = payload.last_success_at ? minutesSinceRefresh(payload.last_success_at) : null;
    const minuteLabel = elapsedMinutes === 1 ? "minute" : "minutes";
    const message = elapsedMinutes !== null
      ? `Updated ${number.format(elapsedMinutes)} ${minuteLabel} ago · ${availabilityChanges} availability changes`
      : `Update unavailable · ${availabilityChanges} availability changes`;
    sectionRefreshStatus.dataset.state = payload.state || "unknown";
    sectionRefreshStatus.textContent = message;
  } catch {
    sectionRefreshStatus.dataset.state = "unknown";
    sectionRefreshStatus.textContent = "Section refresh status is unavailable.";
  }
  sectionRefreshStatus.hidden = false;
}

function metadata(label, value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }

  return `<p class="metadata"><strong>${label}:</strong> ${escapeHtml(value)}</p>`;
}

function courseReferenceMarkup(value) {
  const source = String(value ?? "");
  const matcher = /\b([A-Z]{2,5})\s*-?\s*(\d{3}[A-Z]?)\b/g;
  let output = "";
  let cursor = 0;
  let match;
  while ((match = matcher.exec(source))) {
    output += escapeHtml(source.slice(cursor, match.index));
    const subject = match[1].toUpperCase();
    const identifier = `${subject}-${match[2].toUpperCase()}`;
    output += `<a class="course-reference-link" href="/course/${encodeURIComponent(identifier)}">${escapeHtml(match[0])}</a>`;
    cursor = matcher.lastIndex;
  }
  return output + escapeHtml(source.slice(cursor));
}

function requirementMarkup(label, value) {
  if (value === null || value === undefined || value === "") return "";
  return `<div class="prerequisite-note"><strong>${escapeHtml(label)}</strong><span>${courseReferenceMarkup(value)}</span></div>`;
}

function createSubjectTrie(subjects) {
  const root = { children: new Map(), subjects: [] };
  subjects.forEach((subject) => {
    let node = root;
    node.subjects.push(subject);
    Array.from(subject.code).forEach((letter) => {
      const key = letter.toUpperCase();
      if (!node.children.has(key)) {
        node.children.set(key, { children: new Map(), subjects: [] });
      }
      node = node.children.get(key);
      node.subjects.push(subject);
    });
  });
  return root;
}

function subjectPrefixMatches(prefix) {
  let node = subjectTrie;
  const normalizedPrefix = prefix.trim().toUpperCase();
  if (!normalizedPrefix) {
    return subjectOptions;
  }

  const codeMatches = [];
  for (const letter of normalizedPrefix) {
    node = node.children.get(letter);
    if (!node) {
      break;
    }
  }
  if (node) {
    codeMatches.push(...node.subjects);
  }

  const nameMatches = subjectOptions.filter((subject) =>
    subject.name
      .toUpperCase()
      .split(/\s+/)
      .some((word) => word.startsWith(normalizedPrefix)),
  );
  return subjectOptions.filter((subject) => codeMatches.includes(subject) || nameMatches.includes(subject));
}

function normalizeSubjectOptions(subjects) {
  return subjects
    .map((subject) => {
      if (typeof subject === "string") {
        return { code: subject, name: subject };
      }
      const code = String(subject?.code || "").trim().toUpperCase();
      return { code, name: String(subject?.name || code).trim() };
    })
    .filter((subject) => subject.code);
}

function customSelectOptions(select) {
  return Array.from(select?.options || []).map((option) => ({
    value: option.value,
    label: option.textContent.trim(),
  }));
}

function closeCustomSelect(state) {
  if (!state) {
    return;
  }
  state.menu.classList.add("hidden");
  state.trigger.setAttribute("aria-expanded", "false");
  state.activeIndex = -1;
}

function closeAllCustomSelects(except = null) {
  customSelectStates.forEach((state) => {
    if (state !== except) {
      closeCustomSelect(state);
    }
  });
}

function setCustomSelectActiveIndex(state, nextIndex) {
  const options = Array.from(state.menu.querySelectorAll(".custom-select-option"));
  if (options.length === 0) {
    state.activeIndex = -1;
    return;
  }

  state.activeIndex = (nextIndex + options.length) % options.length;
  options.forEach((option, index) => {
    const isActive = index === state.activeIndex;
    option.classList.toggle("active", isActive);
    option.setAttribute("aria-selected", String(isActive));
  });
}

function syncCustomSelect(select) {
  const state = customSelectStates.get(select);
  if (!state) {
    return;
  }

  const options = customSelectOptions(select);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === select.value));
  const selected = options[selectedIndex];
  state.trigger.querySelector(".custom-select-value").textContent = selected?.label || "";
  state.menu.innerHTML = options
    .map(
      (option, index) => `
        <button
          class="custom-select-option${index === selectedIndex ? " active" : ""}"
          type="button"
          role="option"
          data-value="${escapeHtml(option.value)}"
          aria-selected="${index === selectedIndex ? "true" : "false"}"
        >${escapeHtml(option.label)}</button>
      `,
    )
    .join("");
  if (state.menu.classList.contains("hidden")) {
    state.activeIndex = -1;
  } else {
    setCustomSelectActiveIndex(state, selectedIndex);
  }
}

function openCustomSelect(state) {
  closeAllCustomSelects(state);
  closeSubjectDropdown();
  closeMajorDropdown();
  state.menu.classList.remove("hidden");
  state.trigger.setAttribute("aria-expanded", "true");
  const selectedIndex = customSelectOptions(state.select).findIndex((option) => option.value === state.select.value);
  setCustomSelectActiveIndex(state, selectedIndex >= 0 ? selectedIndex : 0);
}

function chooseCustomSelectOption(state, value) {
  state.select.value = value;
  syncCustomSelect(state.select);
  closeCustomSelect(state);
  state.select.dispatchEvent(new Event("change", { bubbles: true }));
  state.trigger.focus();
}

function initializeCustomSelect(select) {
  if (!select || customSelectStates.has(select)) {
    return;
  }

  const wrapper = select.closest(".custom-select");
  if (!wrapper) {
    return;
  }

  const menuId = `${select.id}-options`;
  const trigger = document.createElement("button");
  trigger.id = `${select.id}-trigger`;
  trigger.className = "filter-control custom-select-trigger";
  trigger.type = "button";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", menuId);
  trigger.innerHTML = `<span class="custom-select-value"></span>`;

  const menu = document.createElement("div");
  menu.id = menuId;
  menu.className = "custom-select-menu filter-menu hidden";
  menu.setAttribute("role", "listbox");

  select.classList.add("custom-select-source");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  wrapper.append(trigger, menu);
  const state = { select, trigger, menu, activeIndex: -1 };
  customSelectStates.set(select, state);

  trigger.addEventListener("click", () => {
    if (menu.classList.contains("hidden")) {
      openCustomSelect(state);
    } else {
      closeCustomSelect(state);
    }
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeCustomSelect(state);
      return;
    }
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (menu.classList.contains("hidden")) {
        openCustomSelect(state);
      } else if (state.activeIndex >= 0) {
        const option = menu.querySelectorAll(".custom-select-option")[state.activeIndex];
        if (option) {
          chooseCustomSelectOption(state, option.dataset.value);
        }
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.classList.contains("hidden")) {
        openCustomSelect(state);
      } else {
        setCustomSelectActiveIndex(state, state.activeIndex + (event.key === "ArrowDown" ? 1 : -1));
      }
    }
  });
  menu.addEventListener("click", (event) => {
    const option = event.target.closest(".custom-select-option");
    if (option) {
      chooseCustomSelectOption(state, option.dataset.value);
    }
  });
  select.addEventListener("change", () => syncCustomSelect(select));
  syncCustomSelect(select);
}

function initializeCustomSelects() {
  form.querySelectorAll(".custom-select > select").forEach(initializeCustomSelect);
}

function syncAllCustomSelects() {
  customSelectStates.forEach((state) => syncCustomSelect(state.select));
}

async function loadFilterOptions() {
  try {
    const response = await fetch("/filter-options");
    if (!response.ok) {
      throw new Error(`Filter options failed with status ${response.status}`);
    }
    const payload = await response.json();
    if (Array.isArray(payload.subjects) && payload.subjects.length > 0) {
      subjectOptions = normalizeSubjectOptions(payload.subjects);
      subjectTrie = createSubjectTrie(subjectOptions);
    }
    if (Array.isArray(payload.majors) && payload.majors.length > 0) {
      majorOptions = payload.majors;
    }
  } catch {
    subjectOptions = FALLBACK_SUBJECTS;
    majorOptions = FALLBACK_MAJORS;
    subjectTrie = createSubjectTrie(subjectOptions);
  }
}

function closeSubjectDropdown() {
  subjectOptionsElement.classList.add("hidden");
  prefixInput.setAttribute("aria-expanded", "false");
  activeSubjectIndex = -1;
}

function renderSubjectOptions() {
  const matches = subjectPrefixMatches(prefixInput.value);
  activeSubjectIndex = matches.length > 0 ? 0 : -1;
  prefixInput.setAttribute("aria-expanded", "true");
  subjectOptionsElement.classList.remove("hidden");
  subjectOptionsElement.innerHTML =
    matches.length === 0
      ? `<div class="subject-option-empty">No matching subjects</div>`
      : matches
          .map(
            (subject, index) => `
              <button
                class="subject-option${index === activeSubjectIndex ? " active" : ""}"
                type="button"
                role="option"
                data-subject="${escapeHtml(subject.code)}"
                aria-selected="${index === activeSubjectIndex ? "true" : "false"}"
              >
                <span class="subject-option-code">${escapeHtml(subject.code)}</span>
                <span class="subject-option-name">${escapeHtml(subject.name)}</span>
              </button>
            `,
          )
          .join("");
}

function setActiveSubjectIndex(nextIndex) {
  const options = Array.from(subjectOptionsElement.querySelectorAll(".subject-option"));
  if (options.length === 0) {
    activeSubjectIndex = -1;
    return;
  }

  activeSubjectIndex = (nextIndex + options.length) % options.length;
  options.forEach((option, index) => {
    const isActive = index === activeSubjectIndex;
    option.classList.toggle("active", isActive);
    option.setAttribute("aria-selected", String(isActive));
  });
  options[activeSubjectIndex].scrollIntoView({ block: "nearest" });
}

function selectSubject(subject) {
  prefixInput.value = subject;
  closeSubjectDropdown();
  runSearch();
}

function closeMajorDropdown() {
  majorOptionsElement.classList.add("hidden");
  studentMajorInput.setAttribute("aria-expanded", "false");
  activeMajorIndex = -1;
}

function matchingMajors(query) {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return majorOptions.slice(0, 50);
  }

  const prefixMatches = majorOptions.filter((major) => major.toLowerCase().startsWith(normalizedQuery));
  const containsMatches = majorOptions.filter(
    (major) =>
      !prefixMatches.includes(major) &&
      major.toLowerCase().includes(normalizedQuery),
  );
  return prefixMatches.concat(containsMatches).slice(0, 50);
}

function renderMajorOptions() {
  closeAllCustomSelects();
  closeSubjectDropdown();
  const matches = matchingMajors(studentMajorInput.value);
  activeMajorIndex = matches.length > 0 ? 0 : -1;
  studentMajorInput.setAttribute("aria-expanded", "true");
  majorOptionsElement.classList.remove("hidden");
  majorOptionsElement.innerHTML =
    matches.length === 0
      ? `<div class="subject-option-empty">No matching majors</div>`
      : matches
          .map(
            (major, index) => `
              <button
                class="subject-option${index === activeMajorIndex ? " active" : ""}"
                type="button"
                role="option"
                data-major="${escapeHtml(major)}"
                aria-selected="${index === activeMajorIndex ? "true" : "false"}"
              >${escapeHtml(major)}</button>
            `,
          )
          .join("");
}

function setActiveMajorIndex(nextIndex) {
  const options = Array.from(majorOptionsElement.querySelectorAll(".subject-option"));
  if (options.length === 0) {
    activeMajorIndex = -1;
    return;
  }

  activeMajorIndex = (nextIndex + options.length) % options.length;
  options.forEach((option, index) => {
    const isActive = index === activeMajorIndex;
    option.classList.toggle("active", isActive);
    option.setAttribute("aria-selected", String(isActive));
  });
  options[activeMajorIndex].scrollIntoView({ block: "nearest" });
}

function selectMajor(major) {
  studentMajorInput.value = major;
  closeMajorDropdown();
  runSearch();
}

function selectedMajorValue() {
  const normalizedValue = studentMajorInput.value.trim().toLowerCase();
  return majorOptions.find((major) => major.toLowerCase() === normalizedValue) || "";
}

function checkedInputs(name) {
  return Array.from(form.querySelectorAll(`input[name="${name}"]:checked`));
}

function selectedTerms() {
  const termSelect = form.querySelector('select[name="term"]');
  return termSelect?.value ? [termSelect.value] : [];
}

function selectedTermLabels() {
  const termSelect = form.querySelector('select[name="term"]');
  const selected = termSelect?.selectedOptions[0];
  return selected?.value ? [filterLabel(selected)] : [];
}

function usesDefaultTerms() {
  const terms = selectedTerms();
  return terms.length === DEFAULT_TERMS.length && DEFAULT_TERMS.every((term) => terms.includes(term));
}

function filterLabel(input) {
  return input.dataset.chipLabel || input.value;
}

function displayAttribute(attribute) {
  return ATTRIBUTE_LABELS[attribute] || attribute;
}

function uniqueSorted(values) {
  return Array.from(new Set(values.filter(Boolean))).sort((left, right) => left.localeCompare(right));
}

function updateMultiSelectSummaries() {
  const catalogScope = selectedTerms().length === 0;
  const termDependentFilters = new Set([
    "location",
    "instruction_type",
    "core",
    "graduation_requirement",
  ]);

  filterSummaryElements.forEach((summary, name) => {
    if (catalogScope && termDependentFilters.has(name)) {
      summary.textContent = "Catalog-wide";
      return;
    }

    const selected = checkedInputs(name).map(filterLabel);
    const emptyLabel =
      summary.dataset.emptyLabel ||
      (name === "location" ? "All campuses" : "Any");
    summary.textContent =
      selected.length === 0
        ? emptyLabel
        : selected.length === 1
          ? selected[0]
          : `${selected.length} selected`;
  });
}

function formatTagLabel(instructionType) {
  const normalized = String(instructionType || "").toLowerCase();
  if (normalized.includes("web based")) {
    return "Web Based";
  }
  if (normalized.includes("non-traditional")) {
    return "Non-traditional";
  }
  if (normalized.includes("traditional") || normalized.includes("face-to-face")) {
    return "Traditional";
  }
  if (normalized.includes("co-operative education")) {
    return "Co-op";
  }
  if (normalized.includes("student teaching")) {
    return "Student Teaching";
  }
  if (normalized.includes("study abroad")) {
    return "Study Abroad";
  }
  if (normalized.includes("synchronous video")) {
    return "Synchronous Video/Web";
  }
  if (normalized.includes("hybrid")) {
    return "Hybrid/Blended";
  }
  if (normalized.includes("mixed")) {
    return "Mixed F2F & Remote";
  }
  return instructionType;
}

function attributeTagClass(attribute) {
  const normalized = String(attribute || "").toLowerCase();
  if (normalized.includes("core") || normalized.includes("univ req")) {
    return "tag-ucc";
  }
  return "tag-other";
}

function attributeTagOrder(attribute) {
  const normalized = String(attribute || "").toLowerCase();
  if (normalized.includes("core")) return 0;
  if (normalized.includes("univ req")) return 1;
  return 2;
}

function currentSectionLocations(course) {
  const sections = course.matching_current_sections || course.current_sections || [];
  return uniqueSorted(
    sections.flatMap((section) => section.filter_locations || []),
  );
}

function currentSectionFormats(course) {
  const sections = course.matching_current_sections || course.current_sections || [];
  return uniqueSorted(
    sections.map((section) => formatTagLabel(section.instruction_type || "")),
  );
}

function isLocationAttribute(attribute, locations) {
  const normalized = String(attribute || "").trim().toLocaleLowerCase();
  return normalized && locations.some((location) => String(location).trim().toLocaleLowerCase() === normalized);
}

function renderCourseTags(course) {
  const tags = [];
  const sections = course.matching_current_sections || course.current_sections || [];
  const locations = currentSectionLocations(course);
  const locationAttributeValues = uniqueSorted([
    ...locations,
    ...sections.map((section) => section.site),
  ]);
  const formats = currentSectionFormats(course);
  if (locations.length) tags.push({ label: locations.join(" · "), className: "tag-location", icon: "📍" });
  if (formats.length) tags.push({ label: formats.join(" · "), className: "tag-format", icon: "◫" });
  // Keep location values in the response for indexing and filtering, but do not
  // repeat them as generic attributes after showing the location tag.
  const attributes = (course.course_attributes || [])
    .filter((attribute) => !isLocationAttribute(attribute, locationAttributeValues))
    .map((attribute, index) => ({ attribute, index }))
    .sort((left, right) => attributeTagOrder(left.attribute) - attributeTagOrder(right.attribute) || left.index - right.index);
  const uccAttributes = attributes.filter(({ attribute }) => attributeTagClass(attribute) === "tag-ucc").map(({ attribute }) => displayAttribute(attribute));
  if (uccAttributes.length) tags.push({ label: uccAttributes.join(" · "), className: "tag-ucc", icon: "◆" });
  attributes.filter(({ attribute }) => attributeTagClass(attribute) !== "tag-ucc").forEach(({ attribute }) => {
    tags.push({ label: displayAttribute(attribute), className: "tag-other", icon: "" });
  });

  const seen = new Set();
  const uniqueTags = tags
    .filter((tag) => {
      const key = tag.label.toLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });

  if (!uniqueTags.length) {
    return "";
  }

  const tagMarkup = (tag) => `<span class="course-tag ${tag.className}">${tag.icon ? `<i aria-hidden="true">${tag.icon}</i>` : ""}${escapeHtml(tag.label)}</span>`;
  const visibleTags = uniqueTags.filter((tag) => tag.className !== "tag-other");
  const otherTags = uniqueTags.filter((tag) => tag.className === "tag-other");
  const attributeCount = locations.length + formats.length + attributes.length;
  const collapseOtherAttributes = otherTags.length > 0 && attributeCount > 6;
  const displayedTags = collapseOtherAttributes ? visibleTags : uniqueTags;
  const moreTag = collapseOtherAttributes ? `<span class="course-tag tag-more">+${otherTags.length} more</span>` : "";
  const visibleMarkup = displayedTags.map(tagMarkup).join('<span class="tag-divider" aria-hidden="true">|</span>');
  return `<span class="course-tags" aria-label="Course attributes">${visibleMarkup}${moreTag ? `${visibleMarkup ? '<span class="tag-divider" aria-hidden="true">|</span>' : ""}${moreTag}` : ""}</span>`;
}

function gpaClass(avgGpa) {
  if (avgGpa === null || avgGpa === undefined) {
    return "gpa-neutral";
  }
  if (avgGpa > 3.5) {
    return "gpa-green";
  }
  if (avgGpa > 3.0) {
    return "gpa-yellow";
  }
  return "gpa-red";
}

function renderMetricScope(metrics) {
  const scope = metrics.scope;
  if (!scope) {
    return "";
  }

  const applied = (scope.applied_filters || []).map((filter) => METRIC_FILTER_LABELS[filter] || filter);
  const unsupported = (scope.unsupported_filters || []).map((filter) => METRIC_FILTER_LABELS[filter] || filter);
  const pieces = [];
  pieces.push(applied.length > 0 ? `Filtered metrics: ${applied.join(", ")}` : "Filtered metrics: all locations and formats");
  if (unsupported.length > 0) {
    pieces.push(`Course-only filters: ${unsupported.join(", ")}`);
  }
  return `<div class="metric-scope">${escapeHtml(pieces.join(" | "))}</div>`;
}

function renderGradeMetrics(metrics) {
  if (!metrics) {
    return "";
  }

  const hasGpa = metrics.avg_gpa !== null && metrics.avg_gpa !== undefined;
  const gpaText = hasGpa ? Number(metrics.avg_gpa).toFixed(2) : "No GPA";
  const comparison =
    Array.isArray(metrics.semester_labels) && metrics.semester_labels.length > 0
      ? `${metrics.semester_labels.join(", ")} ${metrics.comparison_year || ""}`.trim()
      : [metrics.comparison_semester, metrics.comparison_year].filter(Boolean).join(" ");
  const enrollment = Number(metrics.total_enrollment || 0).toLocaleString();
  const sections = Number(metrics.sections_observed || 0).toLocaleString();

  return `
    <div class="metrics-row">
      <span class="gpa-badge ${gpaClass(metrics.avg_gpa)}">${escapeHtml(gpaText)}</span>
      <span class="metric"><strong>Prior term:</strong> ${escapeHtml(comparison || "Unavailable")}</span>
      <span class="metric"><strong>Historical enrollment:</strong> ${escapeHtml(enrollment)}</span>
      <span class="metric"><strong>Grade sections:</strong> ${escapeHtml(sections)}</span>
    </div>
    ${renderMetricScope(metrics)}
  `;
}

function renderSemesterBreakdown(course) {
  const selected = course.selected_grade_metrics?.semesters || [];
  const bySemester = course.selected_grade_metrics?.by_semester || course.grade_metrics_by_semester || {};
  const rows = selected
    .map((semester) => bySemester[semester])
    .filter(Boolean)
    .filter((metrics) => metrics.has_data)
    .map((metrics) => {
      const gpa = metrics.avg_gpa === null || metrics.avg_gpa === undefined
        ? "No GPA"
        : Number(metrics.avg_gpa).toFixed(2);
      const enrollment = Number(metrics.total_enrollment || 0).toLocaleString();
      return `<span class="semester-metric semester-${escapeHtml(metrics.semester || "")}">${escapeHtml(metrics.comparison_semester)} ${escapeHtml(metrics.comparison_year || "")}: ${escapeHtml(gpa)}, ${escapeHtml(enrollment)} enrolled</span>`;
    });

  if (rows.length === 0) {
    return "";
  }

  return `<div class="semester-breakdown">${rows.join("")}</div>`;
}

function sectionLabel(section) {
  const pieces = [];
  const terms = section.filter_terms || [];
  if (terms.length > 0) {
    pieces.push(terms.join(", "));
  } else if (section.term_code) {
    pieces.push(section.term_code);
  }
  if (section.section) {
    pieces.push(`Section ${section.section}`);
  }
  if (section.crn) {
    pieces.push(`CRN ${section.crn}`);
  }
  if (section.site) {
    pieces.push(section.site);
  }
  return pieces.join(" | ") || "Section";
}

function subjectNameForCourse(course) {
  const coursePrefix = String(
    course.course_prefix
      || course.subject
      || String(course.course_code || "").match(/^[A-Za-z]{2,8}/)?.[0]
      || "",
  ).trim().toUpperCase();
  if (!coursePrefix) {
    return "";
  }

  return subjectOptions.find((subject) => subject.code === coursePrefix)?.name || coursePrefix;
}

function availabilitySections(course, catalogScope = false) {
  const allSections = course.matching_current_sections || course.current_sections || [];
  const metrics = course.selected_grade_metrics || course.grade_metrics || {};
  const currentTermCode = String(metrics.current_term_code || "202631");
  return catalogScope
    ? allSections.filter((section) => String(section.term_code || "") === currentTermCode)
    : allSections;
}

function sectionAvailabilityTitle(course, catalogScope = false) {
  const sections = availabilitySections(course, catalogScope)
    .map((section) => ({
      section,
      status: String(section.seat_status_open ?? "").trim().toUpperCase(),
    }))
    .filter(({ status }) => status === "Y" || status === "N");
  if (sections.length === 0) {
    return "";
  }

  return `Matching filtered sections: ${sections
    .map(({ section, status }) => {
      const seats = section.seats_available ? `${section.seats_available} seats` : "";
      const details = [sectionLabel(section), seats].filter(Boolean).join(" | ");
      return `${status === "Y" ? "Open" : "Closed"}: ${details}`;
    })
    .join("; ")}`;
}

function renderSectionAvailability(course, catalogScope = false) {
  const selectedOfferingTerms = selectedTerms().map((term) => term.toLowerCase());
  if (!catalogScope && selectedOfferingTerms.some((term) => term === "spring" || term === "summer")) {
    return '<span class="section-availability-text unavailable"><i></i>Archived</span>';
  }
  const sections = availabilitySections(course, catalogScope);
  if (catalogScope && sections.length === 0) {
    return '<span class="section-availability-text unavailable"><i></i>Not currently offered</span>';
  }
  const sectionsWithSeats = sections
    .map((section) => ({ section, status: String(section.seat_status_open ?? "").trim().toUpperCase() }))
    .filter(({ status }) => status === "Y" || status === "N");
  if (sectionsWithSeats.length === 0) {
    return '<span class="section-availability-text unavailable"><i></i>Section status unavailable</span>';
  }
  const open = sectionsWithSeats.filter(({ status }) => status === "Y").length;
  const total = sectionsWithSeats.length;
  const title = sectionAvailabilityTitle(course, catalogScope);
  const titleAttribute = title ? ` title="${escapeHtml(title)}"` : "";
  if (open > 0) {
    return `<span class="section-availability-text open"${titleAttribute}><i></i>${total === 1 ? "1 section open" : `${open} of ${total} sections open`}</span>`;
  }
  return `<span class="section-availability-text closed"${titleAttribute}><i></i>${total === 1 ? "1 section closed" : `All ${total} sections closed`}</span>`;
}

function hasOpenSections(course, catalogScope) {
  return renderSectionAvailability(course, catalogScope).includes("section-availability-text open");
}

function renderCourseMeta(course) {
  const pieces = [];
  if (course.credit_hours) {
    pieces.push(`${course.credit_hours} credits`);
  }
  pieces.push(selectedOfferingLabel());
  const locations = currentSectionLocations(course);
  if (locations.length > 0) {
    pieces.push(locations.join(", "));
  }
  const formats = currentSectionFormats(course);
  if (formats.length > 0) {
    pieces.push(formats.join(", "));
  }
  return `<p class="course-meta">${pieces.map(escapeHtml).join('<span aria-hidden="true">•</span>')}</p>`;
}

function renderCourseInlineMetrics(course) {
  const metrics = course.selected_grade_metrics || course.grade_metrics || {};
  const hasGpa = metrics.avg_gpa !== null && metrics.avg_gpa !== undefined;
  const hasPriorData = hasGpa
    || Number(metrics.total_enrollment || 0) > 0
    || Number(metrics.sections_observed || 0) > 0;
  const gpa = hasGpa ? Number(metrics.avg_gpa).toFixed(2) : "N/A";
  const enrollment = formatInlineEnrollment(metrics.total_enrollment);
  const semesters = Array.isArray(metrics.semester_labels) ? metrics.semester_labels : [];
  const comparison = semesters.length > 0
    ? `${semesters.join(", ")} ${metrics.comparison_year || ""}`.trim()
    : [metrics.comparison_semester, metrics.comparison_year].filter(Boolean).join(" ") || "Prior term";
  if (!hasPriorData) {
    return "";
  }

  const pastTerm = comparison === "Prior term" ? "a prior term" : comparison;
  return `
    <div class="course-inline-metrics" aria-label="Prior term course data">
      <span class="inline-metric"><span class="inline-metric-value">${escapeHtml(gpa)}</span> GPA</span>
      <span aria-hidden="true">·</span>
      <span class="inline-metric"><span class="inline-metric-value">${escapeHtml(enrollment)}</span> enrolled in ${escapeHtml(pastTerm)}</span>
    </div>
  `;
}

function formatInlineEnrollment(value) {
  const enrollment = Number(value || 0);
  if (enrollment < 50) {
    return enrollment.toLocaleString();
  }

  if (enrollment < 100) {
    return "50+";
  }

  if (enrollment < 1000) {
    return `${Math.floor(enrollment / 100) * 100}+`;
  }

  const roundedDown = Math.floor(enrollment / 500) * 500;
  const thousands = roundedDown / 1000;
  return `${Number.isInteger(thousands) ? thousands : thousands.toFixed(1)}k+`;
}

function courseDetailHref(course) {
  const courseId = String(course.course_id || course.course_code || "").replaceAll(" ", "-");
  return `/course/${encodeURIComponent(courseId)}`;
}

function renderRestrictionPanel(course) {
  const sections = course.matching_current_sections || course.current_sections || [];
  const courseRestriction = String(course.restrictions || "").trim();
  const restrictedSections = sections.filter(
    (section) =>
      String(section.registration_restrictions || "").trim() ||
      (Array.isArray(section.major_restrictions) && section.major_restrictions.length > 0),
  );
  const unavailableCount = sections.filter((section) => section.restriction_data_available === false).length;
  const unrestrictedCount = Math.max(sections.length - restrictedSections.length - unavailableCount, 0);
  const sectionRuleCount = restrictedSections.length;

  return `
    <details class="restriction-panel">
      <summary>
        <span>Restrictions</span>
        <strong>${escapeHtml(sectionRuleCount)} section ${sectionRuleCount === 1 ? "rule" : "rules"}</strong>
      </summary>
      <div class="restriction-body">
        ${
          courseRestriction
            ? `
              <div class="restriction-block">
                <h3>Course</h3>
                <p>${escapeHtml(courseRestriction)}</p>
              </div>
            `
            : ""
        }
        ${
          restrictedSections.length > 0
            ? `
              <div class="restriction-list">
                ${restrictedSections
                  .map((section) => {
                    const majors = Array.isArray(section.major_restrictions) ? section.major_restrictions : [];
                    return `
                      <section class="restriction-row">
                        <h3>${escapeHtml(sectionLabel(section))}</h3>
                        ${
                          section.registration_restrictions
                            ? `<p>${escapeHtml(section.registration_restrictions)}</p>`
                            : ""
                        }
                        ${
                          majors.length > 0
                            ? `<p><strong>Eligible majors:</strong> ${escapeHtml(majors.join(", "))}</p>`
                            : ""
                        }
                      </section>
                    `;
                  })
                  .join("")}
              </div>
            `
            : `<p class="restriction-empty">No section-level restrictions listed for matching sections.</p>`
        }
        ${
          unrestrictedCount > 0
            ? `<p class="restriction-note">${escapeHtml(unrestrictedCount)} matching ${unrestrictedCount === 1 ? "section has" : "sections have"} no section-level restrictions listed.</p>`
            : ""
        }
        ${
          unavailableCount > 0
            ? `<p class="restriction-note">${escapeHtml(unavailableCount)} matching ${unavailableCount === 1 ? "section is" : "sections are"} outside the loaded restrictions file terms.</p>`
            : ""
        }
      </div>
    </details>
  `;
}

function renderSectionCrnPanel(course) {
  const sections = course.matching_current_sections || course.current_sections || [];
  if (sections.length === 0) {
    return "";
  }

  return `
    <details class="section-crn-panel">
      <summary>
        <span>Section CRNs</span>
        <strong>${escapeHtml(sections.length)} ${sections.length === 1 ? "section" : "sections"}</strong>
      </summary>
      <div class="section-crn-list">
        ${sections
          .map((section) => {
            const sectionName = section.section ? `Section ${section.section}` : "Section";
            const term = Array.isArray(section.filter_terms) && section.filter_terms.length > 0
              ? section.filter_terms.join(", ")
              : section.term_code || "";
            const details = [
              term,
              section.site,
              section.instruction_type,
              section.seats_available ? `${section.seats_available} seats` : "",
            ].filter(Boolean);
            return `
              <div class="section-crn-row">
                <span>${escapeHtml(sectionName)}</span>
                <strong>CRN ${escapeHtml(section.crn || "N/A")}</strong>
                ${details.length > 0 ? `<small>${escapeHtml(details.join(" | "))}</small>` : ""}
              </div>
            `;
          })
          .join("")}
      </div>
    </details>
  `;
}

function activeFilterChips() {
  const chips = [];
  const subject = prefixInput.value.trim().toUpperCase();
  const catalogScope = selectedTerms().length === 0;

  if (catalogScope) {
    chips.push({ type: "catalog_scope", label: "Total Course Catalog" });
  } else if (!usesDefaultTerms()) {
    selectedTermLabels().forEach((label, index) => {
      chips.push({ type: "term", value: selectedTerms()[index], label });
    });
  }
  if (subject) {
    chips.push({ type: "subject", label: subject });
  }
  const selectedMajor = selectedMajorValue();
  if (!catalogScope && selectedMajor) {
    chips.push({ type: "student_major", value: selectedMajor, label: selectedMajor });
  }
  const visibleFilterGroups = catalogScope ? ["degree_level"] : CHECKBOX_FILTERS;
  visibleFilterGroups.forEach((name) => {
    checkedInputs(name).forEach((input) => {
      chips.push({ type: name, value: input.value, label: filterLabel(input) });
    });
  });

  return chips;
}

function renderActiveFilters() {
  updateMultiSelectSummaries();
  const chips = activeFilterChips();
  activeFiltersElement.classList.toggle("hidden", chips.length === 0);
  if (chips.length === 0) {
    activeFiltersElement.innerHTML = "";
    return;
  }

  activeFiltersElement.innerHTML = `
    <div class="chip-list">
      ${chips
        .map(
          (chip) => `
            <button class="filter-chip" type="button" data-filter-type="${escapeHtml(chip.type)}" data-filter-value="${escapeHtml(chip.value || "")}">
              ${escapeHtml(chip.label)}
              <span aria-hidden="true">x</span>
            </button>
          `,
        )
        .join("")}
      <button class="clear-filters" type="button">Clear all</button>
    </div>
  `;
}

function renderResults(payload, append = false, elapsedMs = null) {
  const unfilteredResults = payload.results || [];
  const results = openSectionsOnlyInput.checked
    ? unfilteredResults.filter((course) => hasOpenSections(course, Boolean(payload.catalog_scope)))
    : unfilteredResults;
  currentTotal = payload.total ?? 0;
  currentOffset += unfilteredResults.length;
  loadedResults = append ? loadedResults.concat(results) : results;
  const elapsed = typeof elapsedMs === "number" ? ` in ${(elapsedMs / 1000).toFixed(2)} seconds` : "";
  const resultCount = openSectionsOnlyInput.checked
    ? `Showing ${loadedResults.length} courses with open sections`
    : `Showing ${loadedResults.length} of ${currentTotal} matching courses`;
  statusElement.textContent = `${resultCount}${elapsed}`;
  showMoreButton.classList.toggle("hidden", currentOffset >= currentTotal);

  if (loadedResults.length === 0) {
    resultsElement.innerHTML = `<div class="empty">No matching courses found.</div>`;
    return;
  }

  resultsElement.innerHTML = loadedResults
    .map(
      (course) => `
        <article class="course">
          <div class="course-main">
            <header class="course-header">
              <div class="course-title-row">
                <h2 class="course-title"><a class="course-detail-link" href="${courseDetailHref(course)}"><span class="course-code" title="${escapeHtml(subjectNameForCourse(course))}">${escapeHtml(course.course_code)}</span> ${escapeHtml(course.title)}</a>${course.credit_hours ? `<span class="course-credits">${escapeHtml(course.credit_hours)} credit${Number(course.credit_hours) === 1 ? "" : "s"}</span>` : ""}</h2>
                ${renderSectionAvailability(course, Boolean(payload.catalog_scope))}
              </div>
              ${renderCourseInlineMetrics(course)}
            </header>
            <p class="description">${escapeHtml(course.description || "No description available.")}</p>
            ${requirementMarkup("Prerequisites", course.prerequisites)}
            ${requirementMarkup("Cross listings", course.cross_listings)}
            <footer class="course-footer">
              ${renderCourseTags(course)}
            </footer>
          </div>
        </article>
      `,
    )
    .join("");
}

function appendCheckboxValues(params, name) {
  const selected = checkedInputs(name);
  if (name === "location" && selected.length === 0) {
    params.append("location", "all");
    return;
  }

  selected.forEach((input) => {
    params.append(name, input.value);
    if (input.dataset.linkedCore) {
      params.append(name, input.dataset.linkedCore);
    }
  });
}

function buildSearchParams() {
  const params = new URLSearchParams();
  const query = queryInput.value.trim();
  const prefix = prefixInput.value.trim();
  const terms = selectedTerms();
  const catalogScope = terms.length === 0;

  if (query) {
    params.set("q", query);
  }
  if (prefix) {
    params.set("prefix", prefix);
  }
  const selectedMajor = selectedMajorValue();
  if (!catalogScope && selectedMajor) {
    params.set("student_major", selectedMajor);
  }
  terms.forEach((term) => params.append("term", term));

  if (catalogScope) {
    appendCheckboxValues(params, "degree_level");
  } else {
    CHECKBOX_FILTERS.forEach((name) => appendCheckboxValues(params, name));
  }
  params.set("rank", rankInput.value);
  return params;
}

function restoreSearchFromUrl() {
  const params = new URLSearchParams(window.location.search);
  if (advancedFiltersElement) {
    advancedFiltersElement.open = ["instruction_type", "degree_level", "core", "graduation_requirement", "student_major"]
      .some((name) => params.has(name));
  }
  const hasSearchState = ["q", "prefix", "term", "rank", "location", "instruction_type", "core", "graduation_requirement", "degree_level", "student_major"]
    .some((name) => params.has(name));
  if (!hasSearchState) {
    return false;
  }

  resetFilters();
  queryInput.value = params.get("q") || "";
  prefixInput.value = (params.get("prefix") || "").toUpperCase();

  const requestedTerms = params.getAll("term");
  if (requestedTerms.length > 0) {
    const termSelect = form.querySelector('select[name="term"]');
    const requestedTerm = requestedTerms.find((term) =>
      Array.from(termSelect?.options || []).some((option) => option.value === term),
    );
    if (termSelect && requestedTerm !== undefined) {
      termSelect.value = requestedTerm;
    }
  }

  CHECKBOX_FILTERS.forEach((name) => {
    const requestedValues = params.getAll(name);
    if (requestedValues.length === 0) {
      return;
    }
    form.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
      input.checked = requestedValues.includes(input.value);
    });
  });
  const requestedRank = params.get("rank");
  if (requestedRank && Array.from(rankInput.options).some((option) => option.value === requestedRank)) {
    rankInput.value = requestedRank;
  }
  const requestedMajor = params.get("student_major");
  const matchedMajor = majorOptions.find(
    (major) => major.toLowerCase() === String(requestedMajor || "").toLowerCase(),
  );
  if (matchedMajor) {
    studentMajorInput.value = matchedMajor;
  }
  return true;
}

function syncSearchUrl(params) {
  const url = new URL(window.location.href);
  url.search = params.toString();
  const searchPath = `${url.pathname}${url.search}${url.hash}`;
  history.replaceState(null, "", searchPath);
  try {
    sessionStorage.setItem(SEARCH_RETURN_STORAGE_KEY, searchPath);
  } catch {
    // Search and navigation still work when browser storage is unavailable.
  }
}

async function fetchResults(params, append = false) {
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(currentOffset));
  const startedAt = performance.now();

  statusElement.textContent = "Searching...";
  if (!append) {
    resultsElement.innerHTML = "";
    showMoreButton.classList.add("hidden");
  }

  try {
    const response = await fetch(`/search?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Search failed with status ${response.status}`);
    }
    renderResults(await response.json(), append, performance.now() - startedAt);
  } catch (error) {
    statusElement.textContent = "Search is unavailable.";
    resultsElement.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    showMoreButton.classList.add("hidden");
  }
}

async function runSearch() {
  currentOffset = 0;
  loadedResults = [];
  syncAllCustomSelects();
  renderActiveFilters();
  activeParams = buildSearchParams();
  syncSearchUrl(activeParams);
  await fetchResults(new URLSearchParams(activeParams), false);
}

function resetFilters() {
  prefixInput.value = "";
  studentMajorInput.value = "";
  openSectionsOnlyInput.checked = false;
  closeSubjectDropdown();
  closeMajorDropdown();
  const termSelect = form.querySelector('select[name="term"]');
  if (termSelect) {
    termSelect.value = DEFAULT_TERMS[0] || "";
  }
  CHECKBOX_FILTERS.forEach((name) => {
    form.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
      input.checked = name === "location" && DEFAULT_LOCATIONS.includes(input.value);
    });
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch();
});

openSectionsOnlyInput.addEventListener("change", runSearch);

form.addEventListener("change", (event) => {
  if (event.target.matches('input[type="checkbox"], input[type="radio"], select')) {
    runSearch();
  }
});

prefixInput.addEventListener("input", () => {
  prefixInput.value = prefixInput.value.toUpperCase();
  renderActiveFilters();
  renderSubjectOptions();
});

prefixInput.addEventListener("focus", renderSubjectOptions);

prefixInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (subjectOptionsElement.classList.contains("hidden")) {
      renderSubjectOptions();
      return;
    }
    setActiveSubjectIndex(activeSubjectIndex + 1);
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    setActiveSubjectIndex(activeSubjectIndex - 1);
  }
  if (event.key === "Enter" && !subjectOptionsElement.classList.contains("hidden")) {
    const options = Array.from(subjectOptionsElement.querySelectorAll(".subject-option"));
    if (activeSubjectIndex >= 0 && options[activeSubjectIndex]) {
      event.preventDefault();
      selectSubject(options[activeSubjectIndex].dataset.subject);
    }
  }
  if (event.key === "Escape") {
    closeSubjectDropdown();
  }
});

subjectOptionsElement.addEventListener("mousedown", (event) => {
  event.preventDefault();
  const option = event.target.closest(".subject-option");
  if (option) {
    selectSubject(option.dataset.subject);
  }
});

studentMajorInput.addEventListener("input", () => {
  renderActiveFilters();
  renderMajorOptions();
});

studentMajorInput.addEventListener("focus", renderMajorOptions);

studentMajorInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (majorOptionsElement.classList.contains("hidden")) {
      renderMajorOptions();
      return;
    }
    setActiveMajorIndex(activeMajorIndex + 1);
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    setActiveMajorIndex(activeMajorIndex - 1);
  }
  if (event.key === "Enter" && !majorOptionsElement.classList.contains("hidden")) {
    const options = Array.from(majorOptionsElement.querySelectorAll(".subject-option"));
    if (activeMajorIndex >= 0 && options[activeMajorIndex]) {
      event.preventDefault();
      selectMajor(options[activeMajorIndex].dataset.major);
    }
  }
  if (event.key === "Escape") {
    closeMajorDropdown();
  }
});

majorOptionsElement.addEventListener("mousedown", (event) => {
  event.preventDefault();
  const option = event.target.closest(".subject-option");
  if (option) {
    selectMajor(option.dataset.major);
  }
});

document.addEventListener("mousedown", (event) => {
  if (!event.target.closest(".subject-combobox")) {
    closeSubjectDropdown();
  }
  if (!event.target.closest(".major-combobox")) {
    closeMajorDropdown();
  }
  if (!event.target.closest(".custom-select")) {
    closeAllCustomSelects();
  }
});

activeFiltersElement.addEventListener("click", (event) => {
  const clearButton = event.target.closest(".clear-filters");
  if (clearButton) {
    resetFilters();
    runSearch();
    return;
  }

  const chip = event.target.closest(".filter-chip");
  if (!chip) {
    return;
  }

  if (chip.dataset.filterType === "term") {
    const termSelect = form.querySelector('select[name="term"]');
    if (termSelect) {
      termSelect.value = "";
    }
  } else if (chip.dataset.filterType === "catalog_scope") {
    const termSelect = form.querySelector('select[name="term"]');
    if (termSelect) {
      termSelect.value = DEFAULT_TERMS[0] || "";
    }
  } else if (chip.dataset.filterType === "subject") {
    prefixInput.value = "";
  } else if (chip.dataset.filterType === "student_major") {
    studentMajorInput.value = "";
  } else {
    const input = Array.from(form.querySelectorAll(`input[name="${chip.dataset.filterType}"]`)).find(
      (candidate) => candidate.value === chip.dataset.filterValue,
    );
    if (input) {
      input.checked = false;
    }
  }
  runSearch();
});

showMoreButton.addEventListener("click", async () => {
  showMoreButton.disabled = true;
  await fetchResults(new URLSearchParams(activeParams), true);
  showMoreButton.disabled = false;
});

initializeCustomSelects();
loadFilterOptions().finally(() => {
  loadOfferingTermLabels();
  loadSectionRefreshStatus();
  restoreSearchFromUrl();
  renderActiveFilters();
  runSearch();
});
