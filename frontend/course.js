const pageElement = document.querySelector("#course-page");
const backLink = document.querySelector("#back-to-results");
const SEARCH_RETURN_STORAGE_KEY = "crs:return-to-search";
const COURSE_RETURN_STORAGE_KEY = "crs:return-to-course";

const GRADE_LABELS = [
  ["a_plus", "A+"], ["a", "A"], ["a_minus", "A−"],
  ["b_plus", "B+"], ["b", "B"], ["b_minus", "B−"],
  ["c_plus", "C+"], ["c", "C"], ["c_minus", "C−"],
  ["d_plus", "D+"], ["d", "D"], ["d_minus", "D−"], ["f", "F"],
  ["i", "I"], ["s", "S"], ["p", "P"], ["u", "U"], ["q", "Q"], ["x", "X"],
];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function updatePageMetadata(title, description) {
  document.title = title;
  const descriptionTag = document.querySelector('meta[name="description"]');
  if (descriptionTag) descriptionTag.content = description;
  const openGraphTitle = document.querySelector('meta[property="og:title"]');
  const openGraphDescription = document.querySelector('meta[property="og:description"]');
  if (openGraphTitle) openGraphTitle.content = title;
  if (openGraphDescription) openGraphDescription.content = description;
  let canonical = document.querySelector('link[rel="canonical"]');
  if (!canonical) {
    canonical = document.createElement("link");
    canonical.rel = "canonical";
    document.head.append(canonical);
  }
  canonical.href = `${window.location.origin}${window.location.pathname}`;
  let openGraphUrl = document.querySelector('meta[property="og:url"]');
  if (!openGraphUrl) {
    openGraphUrl = document.createElement("meta");
    openGraphUrl.setAttribute("property", "og:url");
    document.head.append(openGraphUrl);
  }
  openGraphUrl.content = canonical.href;
}

function internalPath(value) {
  if (!value || !String(value).startsWith("/") || String(value).startsWith("//")) return "";
  try {
    const url = new URL(String(value), window.location.origin);
    return url.origin === window.location.origin
      ? `${url.pathname}${url.search}${url.hash}`
      : "";
  } catch {
    return "";
  }
}

function readSessionPath(key) {
  try {
    return internalPath(sessionStorage.getItem(key));
  } catch {
    return "";
  }
}

function storeSessionPath(key, value) {
  const path = internalPath(value);
  if (!path) return;
  try {
    sessionStorage.setItem(key, path);
  } catch {
    // Navigation still works when browser storage is unavailable.
  }
}

function removeLegacyFromParameter() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("from")) return;
  url.searchParams.delete("from");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function displayAttribute(value) {
  const labels = {
    "Core Communication (KCOM)": "Communication (KCOM)",
    "Core Mathematics (KMTH)": "Mathematics (KMTH)",
    "Core Life/Physical Sci (KLPS)": "Life & Physical Sciences (KLPS)",
    "Core Lang, Phil, Culture(KLPC)": "Language, Philosophy & Culture (KLPC)",
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
  return labels[value] || value;
}

function attributeTagClass(attribute) {
  const normalized = String(attribute || "").toLowerCase();
  return normalized.includes("core") || normalized.includes("univ req") ? "tag-ucc" : "tag-other";
}

function attributeTagOrder(attribute) {
  const normalized = String(attribute || "").toLowerCase();
  if (normalized.includes("core")) return 0;
  if (normalized.includes("univ req")) return 1;
  return 2;
}

function formatTagLabel(instructionType) {
  const normalized = String(instructionType || "").toLowerCase();
  if (normalized.includes("web based")) return "Web Based";
  if (normalized.includes("non-traditional")) return "Non-traditional";
  if (normalized.includes("traditional") || normalized.includes("face-to-face")) return "Traditional";
  if (normalized.includes("co-operative education")) return "Co-op";
  if (normalized.includes("student teaching")) return "Student Teaching";
  if (normalized.includes("study abroad")) return "Study Abroad";
  if (normalized.includes("synchronous video")) return "Synchronous Video/Web";
  if (normalized.includes("hybrid")) return "Hybrid/Blended";
  if (normalized.includes("mixed")) return "Mixed F2F & Remote";
  return instructionType;
}

function uniqueSorted(values) {
  return Array.from(new Set(values.filter(Boolean))).sort((left, right) => left.localeCompare(right));
}

function renderCourseTags(course) {
  const sections = course.matching_current_sections || course.current_sections || [];
  const locations = uniqueSorted(sections.flatMap((section) => section.filter_locations || []));
  const locationAttributeValues = uniqueSorted([
    ...locations,
    ...sections.map((section) => section.site),
  ]);
  const formats = uniqueSorted(sections.map((section) => formatTagLabel(section.instruction_type || "")));
  const isLocationAttribute = (attribute, locationValues) => {
    const normalized = String(attribute || "").trim().toLocaleLowerCase();
    return normalized && locationValues.some((location) => String(location).trim().toLocaleLowerCase() === normalized);
  };
  const attributes = (course.course_attributes || [])
    .filter((attribute) => !isLocationAttribute(attribute, locationAttributeValues))
    .map((attribute, index) => ({ attribute, index }))
    .sort((left, right) => attributeTagOrder(left.attribute) - attributeTagOrder(right.attribute) || left.index - right.index);
  const uccAttributes = attributes.filter(({ attribute }) => attributeTagClass(attribute) === "tag-ucc").map(({ attribute }) => displayAttribute(attribute));
  const tags = [
    ...(locations.length ? [{ label: locations.join(" · "), className: "tag-location", icon: "📍" }] : []),
    ...(formats.length ? [{ label: formats.join(" · "), className: "tag-format", icon: "◫" }] : []),
    ...(uccAttributes.length ? [{ label: uccAttributes.join(" · "), className: "tag-ucc", icon: "◆" }] : []),
    ...attributes.filter(({ attribute }) => attributeTagClass(attribute) !== "tag-ucc").map(({ attribute }) => ({ label: displayAttribute(attribute), className: "tag-other", icon: "" })),
  ];
  const seen = new Set();
  const uniqueTags = tags.filter((tag) => {
    const key = String(tag.label).toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!uniqueTags.length) return "";
  const tagMarkup = (tag) => `<span class="course-tag ${tag.className}">${tag.icon ? `<i aria-hidden="true">${tag.icon}</i>` : ""}${escapeHtml(tag.label)}</span>`;
  const visibleTags = uniqueTags.filter((tag) => tag.className !== "tag-other");
  const otherTags = uniqueTags.filter((tag) => tag.className === "tag-other");
  const attributeCount = locations.length + formats.length + attributes.length;
  const collapseOtherAttributes = otherTags.length > 0 && attributeCount > 6;
  const displayedTags = collapseOtherAttributes ? visibleTags : uniqueTags;
  const visibleMarkup = displayedTags.map(tagMarkup).join('<span class="tag-divider" aria-hidden="true">|</span>');
  if (!collapseOtherAttributes) return `<div class="course-tags" aria-label="Course attributes">${visibleMarkup}</div>`;
  const otherMarkup = otherTags.map(tagMarkup).join('<span class="tag-divider" aria-hidden="true">|</span>');
  return `<div class="course-tags" aria-label="Course attributes">${visibleMarkup}<span class="course-tags-more-collapsed">${visibleMarkup ? '<span class="tag-divider" aria-hidden="true">|</span>' : ""}<button class="course-tag tag-more tag-more-toggle" type="button" data-action="expand" aria-expanded="false">+${otherTags.length} more</button></span><span class="course-tags-extra"><span class="tag-divider" aria-hidden="true">|</span>${otherMarkup}<span class="tag-divider" aria-hidden="true">|</span><button class="course-tag tag-more tag-more-toggle" type="button" data-action="collapse" aria-expanded="true">Show less</button></span></div>`;
}

function valueOrNA(value) {
  const clean = String(value ?? "").trim();
  return clean && clean.toUpperCase() !== "NA" ? clean : "—";
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
    const number = match[2].toUpperCase();
    output += `<a class="course-reference-link" href="/course/${encodeURIComponent(`${subject}-${number}`)}">${escapeHtml(match[0])}</a>`;
    cursor = matcher.lastIndex;
  }
  return output + escapeHtml(source.slice(cursor));
}

function unlockedCoursesMarkup(courses, visibleLimit = 6) {
  if (!courses?.length) return "";
  const courseLink = (course) => {
    const code = String(course.course_code || course.course_id || "").trim();
    const identifier = String(course.course_id || code.replace(/\s+/, "-")).trim();
    const title = String(course.title || "").trim();
    return `<a class="course-reference-link" href="/course/${encodeURIComponent(identifier)}" title="${escapeHtml(title)}">${escapeHtml(code)}</a>`;
  };
  const linkedCourses = courses.slice(0, visibleLimit).map(courseLink);
  const remaining = courses.length - linkedCourses.length;
  if (!remaining) return linkedCourses.join(", ");
  const remainingCourses = courses.slice(visibleLimit).map(courseLink).join(", ");
  return `<span class="unlocks-courses">${linkedCourses.join(", ")}<span class="unlocks-extra">, ${remainingCourses}.</span> <button class="unlocks-toggle" type="button" data-remaining="${remaining}" aria-expanded="false">+${remaining} more</button></span>`;
}

function statusMarkup(section) {
  if (section.is_archived) return '<span class="section-status archived">Archived</span>';
  const status = String(section.seat_status_open || "").toUpperCase();
  if (status === "Y") return '<span class="section-status open">Open</span>';
  if (status === "N") return '<span class="section-status closed">Closed</span>';
  return '<span class="section-status unavailable">N/A</span>';
}

function sectionAvailabilityMarkup(sections) {
  if (!sections.length) return '<span class="section-availability-text unavailable"><i></i>Not offered</span>';
  const statuses = sections
    .map((section) => String(section.seat_status_open || "").trim().toUpperCase())
    .filter((status) => status === "Y" || status === "N");
  if (!statuses.length) return '<span class="section-availability-text unavailable"><i></i>Section status unavailable</span>';
  const open = statuses.filter((status) => status === "Y").length;
  const total = statuses.length;
  if (open > 0) return `<span class="section-availability-text open"><i></i>${total === 1 ? "1 section open" : `${open} of ${total} sections open`}</span>`;
  return `<span class="section-availability-text closed"><i></i>${total === 1 ? "1 section closed" : `All ${total} sections closed`}</span>`;
}

function restrictionSummary(section) {
  const pieces = [];
  if (section.major_restrictions?.length) pieces.push(`Majors: ${section.major_restrictions.join(", ")}`);
  if (section.registration_restrictions) pieces.push(section.registration_restrictions);
  return pieces.length ? pieces.join(" · ") : "No section restrictions listed";
}

function metadata(label, value) {
  return value ? `<p class="metadata"><strong>${escapeHtml(label)}:</strong> ${escapeHtml(value)}</p>` : "";
}

function professorGpaMarkup(section) {
  const gpas = section.professor_gpas || [];
  if (!gpas.length) return "N/A";
  return gpas.map((professor) => professor.avg_gpa == null ? "N/A" : Number(professor.avg_gpa).toFixed(2)).join(" · ");
}

function professorLinkMarkup(name, key) {
  if (!key) return escapeHtml(name || "—");
  return `<a class="professor-reference-link" href="/professor/${encodeURIComponent(key)}">${escapeHtml(name || "Instructor")}</a>`;
}

function apEquivalencyMarkup(equivalencies) {
  if (!equivalencies?.length) return "";
  const exams = Array.from(new Set(equivalencies.map((equivalency) => `AP ${equivalency.exam}`)));
  return `<div class="prerequisite-note"><strong>AP Equivalency:</strong><span>${exams.map(escapeHtml).join(" · ")}</span></div>`;
}

function instructorLinksMarkup(section) {
  const instructors = section.normalized_instructors || [];
  return instructors.length
    ? instructors.map((instructor) => professorLinkMarkup(instructor.name, instructor.instructor_id)).join(", ")
    : escapeHtml(valueOrNA(section.instructors));
}

function sectionTermLabel(termCode) {
  const value = String(termCode || "");
  const season = { 1: "Spring", 2: "Summer", 3: "Fall" }[value[4]];
  return season && value.length >= 4 ? `${season} ${value.slice(0, 4)}` : value;
}

function isCollegeStationSection(section) {
  return (section.filter_locations || []).some((location) => String(location).toLowerCase() === "college station")
    || String(section.site || "").toLowerCase() === "college station";
}

function collegeStationOfferingLabel(termCode) {
  return `${sectionTermLabel(termCode)} – College Station`;
}

function sectionTermCodes(sections) {
  return Array.from(new Set(sections.map((section) => String(section.term_code || "")).filter(Boolean))).sort((left, right) => right.localeCompare(left));
}

function offeringLabel(termCode, isArchived = false) {
  return `${collegeStationOfferingLabel(termCode)}${isArchived ? " (archived)" : ""}`;
}

function currentSectionsMarkup(sections) {
  if (!sections?.length) return '<p class="empty">No current sections are loaded for this course.</p>';
  return `<div class="table-scroll"><table class="detail-table current-sections-table">
    <thead><tr><th>Status</th><th>Section / CRN</th><th>Section title</th><th>Professor</th><th>Professor GPA</th><th>Format / type</th><th>Campus</th><th>Meeting</th><th>Enrollment</th><th>Seats / waitlist</th><th>Restrictions</th></tr></thead>
    <tbody>${sections.map((section) => {
      const enrollment = [valueOrNA(section.enrollment), valueOrNA(section.max_enrollment)].join(" / ");
      const seats = `Seats: ${valueOrNA(section.seats_available)} · Wait: ${valueOrNA(section.wait_available)}`;
      const meeting = [section.meeting_days, section.meeting_times, section.meeting_locations].filter(Boolean).join(" · ");
      return `<tr>
        <td>${statusMarkup(section)}</td>
        <td><strong>${escapeHtml(valueOrNA(section.section))}</strong><small>CRN ${escapeHtml(valueOrNA(section.crn))}</small></td>
        <td>${escapeHtml(valueOrNA(section.title))}</td>
        <td>${escapeHtml(valueOrNA(section.instructors))}</td>
        <td>${escapeHtml(professorGpaMarkup(section))}</td>
        <td>${escapeHtml([section.instruction_type, section.schedule_type].filter(Boolean).join(" · ") || "—")}</td>
        <td>${escapeHtml(valueOrNA(section.site))}</td>
        <td>${escapeHtml(meeting || "—")}</td>
        <td>${escapeHtml(enrollment)}</td>
        <td>${escapeHtml(seats)}</td>
        <td class="restriction-cell">${escapeHtml(restrictionSummary(section))}</td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

function termHistoryMarkup(history) {
  if (!history.length) return '<p class="empty">No grade-report history is available from 2023 onward.</p>';
  return `<div class="table-scroll"><table class="detail-table"><thead><tr><th>Term</th><th>Course GPA</th><th>Enrollment</th><th>Graded students</th><th>Sections</th></tr></thead>
    <tbody>${history.map((term) => `<tr><td>${escapeHtml(`${term.semester_label} ${term.year}`)}</td><td>${term.avg_gpa == null ? "—" : Number(term.avg_gpa).toFixed(2)}</td><td>${Number(term.total_enrollment || 0).toLocaleString()}</td><td>${Number(term.gpa_weight || 0).toLocaleString()}</td><td>${Number(term.sections_observed || 0).toLocaleString()}</td></tr>`).join("")}</tbody>
  </table></div>`;
}

function termOutcomeDetailsMarkup(outcomes) {
  const fields = [["section", "Section"], ["total", "Total"], ["gpa", "GPA"], ["a", "A"], ["b", "B"], ["c", "C"], ["d", "D"], ["f", "F"], ["q", "Q"]];
  const detail = (label, value) => `<span><b>${label}</b>${value}</span>`;
  return `<div class="section-grade-details"><div class="section-grade-head">${fields.map(([, label]) => `<span>${label}</span>`).join("")}</div>${outcomes.map((outcome) => { const counts = outcome.grade_counts || {}; const values = { section: escapeHtml(valueOrNA(outcome.section)), total: Number(outcome.total_enrollment || 0).toLocaleString(), gpa: outcome.gpa_weight ? (Number(outcome.grade_points_total || 0) / Number(outcome.gpa_weight)).toFixed(2) : "—", a: Number(counts.a || 0).toLocaleString(), b: Number(counts.b || 0).toLocaleString(), c: Number(counts.c || 0).toLocaleString(), d: Number(counts.d || 0).toLocaleString(), f: Number(counts.f || 0).toLocaleString(), q: Number(counts.q || 0).toLocaleString() }; return `<div class="section-grade-row">${fields.map(([key, label]) => detail(label, values[key])).join("")}</div>`; }).join("")}</div>`;
}

function termOutcomeDetailsMarkup(outcomes) {
  const fields = [["professor", "Professor"], ["section", "Section"], ["total", "Total"], ["gpa", "GPA"], ["a", "A"], ["b", "B"], ["c", "C"], ["d", "D"], ["f", "F"], ["q", "Q"]];
  return `<div class="section-grade-details course-section-grade-details"><div class="section-grade-head">${fields.map(([, label]) => `<span>${label}</span>`).join("")}</div>${outcomes.map((outcome) => { const counts = outcome.grade_counts || {}; const section = `${valueOrNA(outcome.section)}${outcome.crn ? ` · CRN ${outcome.crn}` : ""}`; const values = { professor: escapeHtml(valueOrNA(outcome.instructor)), section: escapeHtml(section), total: Number(outcome.total_enrollment || 0).toLocaleString(), gpa: outcome.gpa_weight ? (Number(outcome.grade_points_total || 0) / Number(outcome.gpa_weight)).toFixed(2) : "--", a: Number(counts.a || 0).toLocaleString(), b: Number(counts.b || 0).toLocaleString(), c: Number(counts.c || 0).toLocaleString(), d: Number(counts.d || 0).toLocaleString(), f: Number(counts.f || 0).toLocaleString(), q: Number(counts.q || 0).toLocaleString() }; return `<div class="section-grade-row">${fields.map(([key, label]) => `<span><b>${label}</b>${values[key]}</span>`).join("")}</div>`; }).join("")}</div>`;
}

function professorSummaryMarkup(summaries) {
  if (!summaries.length) return '<p class="empty">No professor GPA history is available from 2023 onward.</p>';
  return `<div class="table-scroll"><table class="detail-table"><thead><tr><th><button class="professor-sort" type="button" data-professor-sort="professor" aria-sort="ascending">Professor <span>↑</span></button></th><th><button class="professor-sort" type="button" data-professor-sort="avg_gpa" aria-sort="none">Course GPA <span>↕</span></button></th><th><span class="confidence-heading"><button class="professor-sort" type="button" data-professor-sort="confidence_gpa" aria-sort="none">Confidence GPA <span>↕</span></button><span class="confidence-info" tabindex="0" role="img" aria-label="Confidence GPA uses enrollment to make small classes less decisive." title="Confidence GPA gives more weight to results with more enrolled students, so one small high-GPA class does not outweigh a strong GPA across many students.">i</span></span></th><th><button class="professor-sort" type="button" data-professor-sort="total_enrollment" aria-sort="none">Enrollment <span>↕</span></button></th><th><button class="professor-sort" type="button" data-professor-sort="gpa_weight" aria-sort="none">Graded students <span>↕</span></button></th><th><button class="professor-sort" type="button" data-professor-sort="sections_observed" aria-sort="none">Sections <span>↕</span></button></th></tr></thead>
    <tbody id="professor-summary-rows">${professorSummaryRows(summaries)}</tbody>
  </table></div>`;
}

function compactSectionFormat(instructionType) {
  return formatTagLabel(instructionType) || "â€”";
}

function splitScheduleValues(value) {
  return String(value || "").split(/\s*(?:;|\||\n)\s*/).map((item) => item.trim()).filter(Boolean);
}

function formatMeetingTime(value) {
  return String(value || "").replace(/\b0(\d):/g, "$1:");
}

function meetingMarkup(section) {
  const weekdayNames = { M: "Mon", T: "Tue", W: "Wed", R: "Thu", F: "Fri", S: "Sat", U: "Sun" };
  const dayGroups = splitScheduleValues(section.meeting_days);
  const times = splitScheduleValues(section.meeting_times);
  const locations = splitScheduleValues(section.meeting_locations);
  const count = Math.max(dayGroups.length, times.length, locations.length);
  if (!count) return "—";
  const lines = [];
  for (let index = 0; index < count; index += 1) {
    const rawDays = dayGroups[index] || dayGroups[0] || "TBA";
    const namedDays = rawDays.match(/Mon|Tue|Wed|Thu|Fri|Sat|Sun/gi);
    const days = namedDays ? namedDays.map((day) => `${day.slice(0, 1).toUpperCase()}${day.slice(1).toLowerCase()}`) : ( /^[MTWRFSU]+$/i.test(rawDays) ? [...rawDays.toUpperCase()].map((day) => weekdayNames[day]).filter(Boolean) : [rawDays] );
    const time = formatMeetingTime(times[index] || times[0] || "TBA");
    const location = locations[index] || locations[0] || "TBA";
    days.forEach((day) => lines.push(`<span><span class="meeting-day">${escapeHtml(day)}</span><em>${escapeHtml(`${time} · ${location}`)}</em></span>`));
  }
  return `<div class="meeting-lines">${lines.join("")}</div>`;
}

function restrictionDetailsMarkup(section) {
  const groups = [
    ["Major", section.major_restrictions || [], section.excluded_major_restrictions || []],
    ["Department", section.department_restrictions || [], section.excluded_department_restrictions || []],
    ["College", section.college_restrictions || [], section.excluded_college_restrictions || []],
  ].filter(([, included, excluded]) => included.length || excluded.length);
  const raw = String(section.registration_restrictions || "").trim();
  if (!groups.length && !raw) return '<span class="restriction-none">None</span>';
  const categories = groups.map(([label]) => label);
  const groupLabels = new Set(groups.map(([label]) => label.toLowerCase()));
  const rawRule = (item) => {
    const match = item.match(/^\s*(Must be enrolled in one of the following|May not be enrolled in one of the following|Cannot be enrolled in one of the following|Must be assigned one of the following)\s+(.+?):\s*(.*?)\s*$/i);
    if (match) {
      const value = match[3];
      // Howdy sometimes returns a rule heading with no values (for example,
      // "May not be enrolled ... Concentrations:").  It imposes no rule.
      if (!value) return null;
      const category = match[2]
        .replace(/^the following\s+/i, "")
        .replace(/\s+$/, "")
        .replace(/s$/i, "")
        .replace(/^Student Attribute$/i, "Student attribute");
      const requirement = /^(May not|Cannot)/i.test(match[1]) ? "May not be" : "Must be";
      return { item, category, label: category, value: `${requirement}: ${value}` };
    }
    if (/\bmajor/i.test(item)) return { item, category: "Major" };
    if (/\bdepartment/i.test(item)) return { item, category: "Department" };
    if (/\bcollege/i.test(item)) return { item, category: "College" };
    if (/\bcohort/i.test(item)) return { item, category: "Cohort" };
    if (/\bstudent attributes?\b/i.test(item)) return { item, category: "Student attribute" };
    if (/\bsite|campus\b/i.test(item)) return { item, category: "Site" };
    if (/\bclass|freshman|sophomore|junior|senior|undergraduate|graduate\b/i.test(item)) return { item, category: "Classification" };
    return { item, category: "Registration details" };
  };
  const rawItems = raw.split(/\s*(?:;|\||\n)\s*/).filter(Boolean).map(rawRule).filter(Boolean);
  rawItems.forEach(({ category }) => {
    if (!groupLabels.has(category.toLowerCase()) && !categories.includes(category)) categories.push(category);
  });
  if (!categories.length) categories.push("Registration");
  const detailRows = groups.map(([label, included, excluded]) => `<div><strong>${escapeHtml(label)}</strong><span>${escapeHtml([included.length ? `Must be: ${included.join(", ")}` : "", excluded.length ? `May not be: ${excluded.join(", ")}` : ""].filter(Boolean).join(" · "))}</span></div>`).join("");
  const rawRows = rawItems.filter(({ category }) => !groupLabels.has(category.toLowerCase())).map(({ item, category, label, value }) => { const match = item.match(/^\s*([^:]+):\s*(.+)$/); return `<div><strong>${escapeHtml(label || (match ? match[1] : category))}</strong><span>${escapeHtml(value || (match ? match[2] : item))}</span></div>`; }).join("");
  return `<details class="section-restrictions"><summary><span class="restriction-chip">${escapeHtml(categories.join(" · "))}</span></summary><div class="restriction-breakdown">${detailRows}${rawRows}</div></details>`;
}

function confidenceGpa(summary, allSummaries) {
  const validSummaries = allSummaries.filter((item) => item.avg_gpa !== null && item.avg_gpa !== undefined && Number(item.total_enrollment) > 0);
  const totalEnrollment = validSummaries.reduce((total, item) => total + Number(item.total_enrollment), 0);
  if (summary.avg_gpa === null || summary.avg_gpa === undefined || !totalEnrollment) return null;
  const courseGpa = validSummaries.reduce((total, item) => total + Number(item.avg_gpa) * Number(item.total_enrollment), 0) / totalEnrollment;
  const priorGpa = Math.min(3.4, Math.max(2.8, courseGpa));
  const enrollment = Number(summary.total_enrollment || 0);
  const priorEnrollment = 30;
  return ((Number(summary.avg_gpa) * enrollment) + (priorGpa * priorEnrollment)) / (enrollment + priorEnrollment);
}

function professorSummaryRows(summaries, allSummaries = summaries) {
  return summaries.map((summary) => { const confidence = confidenceGpa(summary, allSummaries); return `<tr><td>${professorLinkMarkup(summary.professor, summary.professor_key)}</td><td>${summary.avg_gpa == null ? "N/A" : Number(summary.avg_gpa).toFixed(2)}</td><td>${confidence == null ? "N/A" : confidence.toFixed(2)}</td><td>${Number(summary.total_enrollment || 0).toLocaleString()}</td><td>${Number(summary.gpa_weight || 0).toLocaleString()}</td><td>${Number(summary.sections_observed || 0).toLocaleString()}</td></tr>`; }).join("");
}

function gradeBreakdownMarkup(outcome) {
  const counts = outcome.grade_counts || {};
  return `<details class="grade-breakdown"><summary>View full grade distribution</summary><div class="grade-grid">
    ${GRADE_LABELS.map(([key, label]) => `<span><strong>${label}</strong> ${Number(counts[key] || 0).toLocaleString()}</span>`).join("")}
  </div></details>`;
}

function outcomeHistoryMarkup(outcomes) {
  if (!outcomes.length) return '<p class="empty">No professor-section grade reports are available from 2023 onward.</p>';
  return `<div class="table-scroll"><table class="detail-table instructor-history-table"><thead><tr><th>Term</th><th>Professor</th><th>Section</th><th>GPA</th><th>Enrollment</th><th>Graded</th><th>Grade distribution</th></tr></thead>
    <tbody>${outcomes.map((outcome) => `<tr class="${outcome.matches_current_instructor ? "current-instructor-match" : ""}">
      <td>${escapeHtml(`${outcome.semester_label} ${outcome.year}`)}</td>
      <td>${escapeHtml(valueOrNA(outcome.instructor))}${outcome.matches_current_instructor ? '<small class="current-match-label">Current instructor match</small>' : ""}</td>
      <td>${escapeHtml(valueOrNA(outcome.section))}</td>
      <td>${outcome.gpa_weight ? (Number(outcome.grade_points_total || 0) / Number(outcome.gpa_weight)).toFixed(2) : "—"}</td>
      <td>${Number(outcome.total_enrollment || 0).toLocaleString()}</td>
      <td>${Number(outcome.gpa_weight || 0).toLocaleString()}</td>
      <td>${gradeBreakdownMarkup(outcome)}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function renderCourse(payload) {
  const course = payload.course;
  const allCurrentSections = course.current_sections || [];
  const availableSectionTerms = sectionTermCodes(allCurrentSections);
  const defaultSectionTerm = availableSectionTerms.includes("202631") ? "202631" : (availableSectionTerms[0] || "");
  const initialSections = defaultSectionTerm
    ? allCurrentSections.filter((section) => String(section.term_code) === defaultSectionTerm)
    : allCurrentSections;
  const attributes = (course.course_attributes || []).map((attribute) => `<span class="course-tag">${escapeHtml(displayAttribute(attribute))}</span>`).join("");
  updatePageMetadata(
    `${course.course_code} - ${course.title} | Aggie Courses`,
    `Review ${course.course_code} ${course.title} at Texas A&M, including requirements, sections, grade distributions, enrollment trends, and professor information.`,
  );
  pageElement.innerHTML = `
    <section class="course-hero">
      <p class="course-code">${escapeHtml(course.course_code)}</p>
      <h1>${escapeHtml(course.title)}</h1>
      <p class="course-page-meta">${course.credit_hours ? `${escapeHtml(course.credit_hours)} credits` : "Credits unavailable"}</p>
      ${attributes ? `<div class="course-tags">${attributes}</div>` : ""}
      <p class="course-description">${escapeHtml(course.description || "No description available.")}</p>
      ${metadata("Prerequisites", course.prerequisites)}
      ${metadata("Cross listings", course.cross_listings)}
      ${metadata("Course restrictions", course.restrictions)}
    </section>
    <section class="course-detail-section"><div class="detail-section-heading"><div><p class="eyebrow">Current offering</p><h2>Sections</h2></div><div class="section-controls"><label class="section-term-filter">Offering semester<select id="section-term-filter">${availableSectionTerms.length > 1 ? '<option value="">All loaded terms</option>' : ""}${availableSectionTerms.map((termCode) => `<option value="${escapeHtml(termCode)}" ${termCode === defaultSectionTerm ? "selected" : ""}>${escapeHtml(sectionTermLabel(termCode))}</option>`).join("")}</select></label><label class="open-sections-filter"><input id="open-sections-filter" type="checkbox" /> Open sections only</label></div></div><p id="section-count" class="section-count">${initialSections.length} loaded sections</p><div id="current-sections-table">${currentSectionsMarkup(initialSections)}</div></section>
    <section class="course-detail-section"><div class="detail-section-heading"><div><p class="eyebrow">Historical outcomes</p><h2>Course trends by term</h2></div><p>${payload.history.start_year} onward</p></div>${termHistoryMarkup(payload.history.term_history || [])}</section>
    <section class="course-detail-section"><div class="detail-section-heading"><div><p class="eyebrow">Professor performance</p><h2>Overall GPA by professor</h2></div><p>Across all available courses, ${payload.history.start_year} onward</p></div>${professorSummaryMarkup(payload.history.professor_summaries || [])}</section>
    <section class="course-detail-section"><div class="detail-section-heading"><div><p class="eyebrow">Professor and section history</p><h2>Grade reports</h2></div><p>Rows highlighted in green match an instructor on a current section.</p></div>${outcomeHistoryMarkup(payload.history.outcomes || [])}</section>`;

  const outcomesByTerm = new Map();
  (payload.history.outcomes || []).forEach((outcome) => {
    const key = `${outcome.year}-${outcome.semester}`;
    const group = outcomesByTerm.get(key) || [];
    group.push(outcome);
    outcomesByTerm.set(key, group);
  });
  document.querySelectorAll(".course-detail-section").forEach((section) => {
    if (!section.querySelector("h2")?.textContent.includes("Course trends by term")) return;
    section.querySelectorAll("tbody tr").forEach((row) => {
      const term = row.cells[0]?.textContent.trim().split(" ");
      const semester = term?.[0];
      const year = term?.[1];
      const semesterKey = { Spring: "spring", Summer: "summer", Fall: "fall" }[semester];
      const outcomes = outcomesByTerm.get(`${year}-${semesterKey}`) || [];
      if (outcomes.length) row.cells[2]?.insertAdjacentHTML("beforeend", termOutcomeDetailsMarkup(outcomes));
    });
  });

  const sectionTermFilter = document.querySelector("#section-term-filter");
  const openSectionsFilter = document.querySelector("#open-sections-filter");
  const currentSectionsTable = document.querySelector("#current-sections-table");
  const sectionCount = document.querySelector("#section-count");
  const renderFilteredSections = () => {
    const selectedTerm = sectionTermFilter.value;
    let sections = selectedTerm
      ? allCurrentSections.filter((section) => String(section.term_code) === selectedTerm)
      : allCurrentSections;
    if (openSectionsFilter.checked) {
      sections = sections.filter((section) => String(section.seat_status_open || "").toUpperCase() === "Y");
    }
    currentSectionsTable.innerHTML = currentSectionsMarkup(sections);
    sectionCount.textContent = `${sections.length} loaded sections`;
  };
  sectionTermFilter?.addEventListener("change", renderFilteredSections);
  openSectionsFilter?.addEventListener("change", renderFilteredSections);
}

function gpaTone(value) {
  if (value == null) return "neutral";
  if (Number(value) >= 3.35) return "green";
  if (Number(value) >= 2.75) return "gold";
  return "red";
}

function compactSectionsMarkup(sections) {
  if (!sections.length) return '<p class="empty">No sections match these filters.</p>';
  return `<div class="table-scroll"><table class="detail-table current-sections-table compact-sections-table"><thead><tr><th>Status</th><th>Section</th><th>Meeting</th><th>Format</th><th>Instructor</th><th></th></tr></thead><tbody>${sections.map((section) => {
    const meeting = [section.meeting_days, section.meeting_times].filter(Boolean).join(" / ");
    return `<tr><td>${statusMarkup(section)}</td><td><strong>${escapeHtml(valueOrNA(section.section))}</strong><small>CRN ${escapeHtml(valueOrNA(section.crn))} · ${escapeHtml(valueOrNA(section.site))}</small></td><td>${escapeHtml(meeting || "TBA")}<small>${escapeHtml(valueOrNA(section.meeting_locations))}</small></td><td>${escapeHtml([section.instruction_type, section.schedule_type].filter(Boolean).join(" · ") || "—")}</td><td><strong>${escapeHtml(valueOrNA(section.instructors))}</strong><small class="professor-gpa gpa-${gpaTone((section.professor_gpas || [])[0]?.avg_gpa)}">GPA ${escapeHtml(professorGpaMarkup(section))}</small></td><td><button class="section-details" type="button">Details</button></td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function gpaChartMarkup(history) {
  const points = [...history].filter((term) => term.avg_gpa != null).sort((a, b) => `${a.year}${a.semester_label}`.localeCompare(`${b.year}${b.semester_label}`));
  if (points.length < 2) return "";
  const width = 640, height = 150, pad = 24;
  const values = points.map((point) => Number(point.avg_gpa));
  const low = Math.min(2.5, Math.floor((Math.min(...values) - .1) * 10) / 10);
  const high = Math.max(4, Math.ceil((Math.max(...values) + .1) * 10) / 10);
  const coords = values.map((value, index) => ({ x: pad + index * ((width - pad * 2) / (values.length - 1)), y: height - pad - ((value - low) / (high - low)) * (height - pad * 2) }));
  const line = coords.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  return `<div class="gpa-chart"><div class="chart-title"><span>GPA trend</span><small>${escapeHtml(`${points[0].semester_label} ${points[0].year}`)} – ${escapeHtml(`${points.at(-1).semester_label} ${points.at(-1).year}`)}</small></div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Course GPA trend">${[low, (low + high) / 2, high].map((value) => { const y = height - pad - ((value - low) / (high - low)) * (height - pad * 2); return `<line x1="${pad}" x2="${width - pad}" y1="${y}" y2="${y}"/><text x="0" y="${y + 4}">${value.toFixed(1)}</text>`; }).join("")}<path d="${line}"/>${coords.map((point, index) => `<circle cx="${point.x}" cy="${point.y}" r="4"/><text class="chart-value" x="${point.x}" y="${point.y - 10}">${values[index].toFixed(2)}</text>`).join("")}</svg></div>`;
}

function detailedSectionsMarkup(sections, gpaSortDirection = null, sectionSortDirection = null) {
  if (!sections.length) return '<p class="empty">No sections match these filters.</p>';
  const gpaArrow = gpaSortDirection === 1 ? "↑" : (gpaSortDirection === -1 ? "↓" : "↕");
  const gpaSort = gpaSortDirection === 1 ? "ascending" : (gpaSortDirection === -1 ? "descending" : "none");
  const sectionArrow = sectionSortDirection === 1 ? "↑" : (sectionSortDirection === -1 ? "↓" : "↕");
  const sectionSort = sectionSortDirection === 1 ? "ascending" : (sectionSortDirection === -1 ? "descending" : "none");
  return `<div class="table-scroll"><table class="detail-table current-sections-table restored-sections-table"><thead><tr><th>Status</th><th><button class="section-number-sort" type="button" aria-sort="${sectionSort}">Section / CRN <span>${sectionArrow}</span></button></th><th>Section title</th><th>Professor</th><th><button class="section-gpa-sort" type="button" aria-sort="${gpaSort}">Professor GPA <span>${gpaArrow}</span></button></th><th>Format / campus</th><th>Meeting</th><th>Restrictions</th></tr></thead><tbody>${sections.map((section) => {
    const offeredStatus = Number(section.term_code || 0) < 202631 ? '<span class="section-status archived">Archived</span>' : statusMarkup(section);
    return `<tr><td>${offeredStatus}</td><td><strong>${escapeHtml(valueOrNA(section.section))}</strong><small>CRN ${escapeHtml(valueOrNA(section.crn))}</small></td><td>${escapeHtml(valueOrNA(section.title))}</td><td>${instructorLinksMarkup(section)}</td><td><span class="professor-gpa gpa-${gpaTone((section.professor_gpas || [])[0]?.avg_gpa)}">${escapeHtml(professorGpaMarkup(section))}</span></td><td>${escapeHtml(compactSectionFormat(section.instruction_type))}<small>${escapeHtml(valueOrNA(section.site))}</small></td><td>${meetingMarkup(section)}</td><td class="restriction-cell">${restrictionDetailsMarkup(section)}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function sectionPagerMarkup(page, total) {
  if (total <= 8) return "";
  const totalPages = Math.ceil(total / 8);
  const button = (label, targetPage, enabled, ariaLabel) => `<button class="section-page-button ${enabled ? "" : "is-placeholder"}" type="button" data-page-index="${targetPage}" aria-label="${ariaLabel}" title="${ariaLabel}" ${enabled ? "" : "disabled"}>${label}</button>`;
  return `<span class="section-page-label">Page ${page + 1} of ${totalPages}</span>${button("&#171;", 0, page > 0, "First section page")}${button("&#8249;", page - 1, page > 0, "Previous section page")}${button("&#8250;", page + 1, page < totalPages - 1, "Next section page")}${button("&#187;", totalPages - 1, page < totalPages - 1, "Last section page")}`;
}

function trendInstructorIds(sections, outcomes) {
  const sectionInstructorIds = new Set(
    sections
      .flatMap((section) => section.normalized_instructors || [])
      .map((instructor) => instructor.instructor_id)
      .filter(Boolean),
  );
  if (!sectionInstructorIds.size) return sectionInstructorIds;

  const instructorIdsWithHistory = new Set(
    outcomes
      .filter((outcome) => outcome.instructor && Number(outcome.gpa_weight || 0) > 0)
      .flatMap((outcome) => outcome.instructor_ids || [])
      .filter(Boolean),
  );
  return new Set([...sectionInstructorIds].filter((id) => instructorIdsWithHistory.has(id)));
}

function professorTrendMarkup(outcomes, allowedInstructors = null, selectedProfessor = null) {
  const seasonOrder = { Spring: 1, Summer: 2, Fall: 3 };
  const colors = ["#d7191c", "#0072b2", "#7b2cbf", "#e87500", "#008f5a", "#d81b60", "#8a7800", "#00a6d6"];
  const allProfessorNames = [...new Set(outcomes.map((outcome) => String(outcome.instructor || "").trim()).filter(Boolean))].sort((left, right) => left.localeCompare(right));
  const colorForProfessor = (professor) => colors[Math.max(0, allProfessorNames.indexOf(professor)) % colors.length];
  const allUsable = outcomes.filter((outcome) => outcome.instructor && outcome.gpa_weight && Number(outcome.gpa_weight) > 0).map((outcome) => ({ ...outcome, gpa: Number(outcome.grade_points_total || 0) / Number(outcome.gpa_weight), order: Number(outcome.year) * 10 + (seasonOrder[outcome.semester_label] || 0) }));
  const baseUsable = allowedInstructors ? allUsable.filter((outcome) => (outcome.instructor_ids || []).some((id) => allowedInstructors.has(id))) : allUsable;
  const usable = selectedProfessor ? baseUsable.filter((outcome) => outcome.instructor === selectedProfessor) : baseUsable;
  const aggregateByInstructorAndTerm = (reportOutcomes) => {
    const aggregated = new Map();
    reportOutcomes.forEach((outcome) => {
      const key = `${outcome.instructor}::${outcome.order}`;
      const existing = aggregated.get(key) || { ...outcome, grade_points_total: 0, gpa_weight: 0, total_enrollment: 0, section_count: 0 };
      existing.grade_points_total += Number(outcome.grade_points_total || 0);
      existing.gpa_weight += Number(outcome.gpa_weight || 0);
      existing.total_enrollment += Number(outcome.total_enrollment || 0);
      existing.section_count += 1;
      existing.gpa = existing.grade_points_total / existing.gpa_weight;
      aggregated.set(key, existing);
    });
    return [...aggregated.values()];
  };
  const termInstructorOutcomes = aggregateByInstructorAndTerm(usable);
  const allTermInstructorOutcomes = aggregateByInstructorAndTerm(allUsable);
  const counts = new Map(); termInstructorOutcomes.forEach((outcome) => counts.set(outcome.instructor, (counts.get(outcome.instructor) || 0) + 1));
  const sortedProfessors = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  const professors = sortedProfessors;
  const points = termInstructorOutcomes.filter((outcome) => professors.includes(outcome.instructor)).sort((a, b) => a.order - b.order);
  if (!points.length) return '<p class="empty chart-empty">No grade-report history is available for the selected instructors.</p>';
  const terms = [...new Map(baseUsable.sort((a, b) => a.order - b.order).map((point) => [point.order, `${point.semester_label} ${point.year}`])).entries()];
  const width = 760, height = 380, pad = { left: 48, right: 18, top: 32, bottom: 44 };
  const observedLow = Math.min(...allTermInstructorOutcomes.map((point) => point.gpa));
  const observedHigh = Math.max(...allTermInstructorOutcomes.map((point) => point.gpa));
  const padding = Math.max(0.1, (observedHigh - observedLow) * 0.15);
  let yLow = Math.max(0, Math.floor((observedLow - padding) * 10) / 10);
  let yHigh = Math.min(4, Math.ceil((observedHigh + padding) * 10) / 10);
  if (yHigh - yLow < 0.4) {
    const midpoint = (observedLow + observedHigh) / 2;
    yLow = Math.max(0, Math.floor((midpoint - 0.2) * 10) / 10);
    yHigh = Math.min(4, Math.ceil((midpoint + 0.2) * 10) / 10);
  }
  const axisValues = Array.from({ length: 5 }, (_, index) => yLow + ((yHigh - yLow) * index) / 4);
  const axisPrecision = yHigh - yLow < 1 ? 2 : 1;
  const xInset = 16;
  const x = (order) => pad.left + xInset + (terms.findIndex(([term]) => term === order) * ((width - pad.left - pad.right - xInset * 2) / Math.max(terms.length - 1, 1)));
  const y = (gpa) => height - pad.bottom - ((gpa - yLow) / (yHigh - yLow)) * (height - pad.top - pad.bottom);
  const series = professors.map((professor) => points.filter((point) => point.instructor === professor));
  return `<div class="professor-trend"><div class="chart-title"><span>Instructor GPA over time</span><small>All sections combined within each term</small></div><div class="trend-legend">${professors.map((professor) => `<span class="trend-legend-professor" role="button" tabindex="0" data-professor="${escapeHtml(professor)}" aria-pressed="${String(selectedProfessor === professor)}"><i style="background:${colorForProfessor(professor)}"></i>${escapeHtml(professor)}</span>`).join("")}</div><div class="trend-canvas"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Instructor GPA trends over time"><text class="y-axis-label" x="14" y="${height / 2}" transform="rotate(-90 14 ${height / 2})">GPA</text>${axisValues.map((value) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}"/><text class="y-axis-tick" x="${pad.left - 6}" y="${y(value) + 4}">${value.toFixed(axisPrecision)}</text>`).join("")}${series.map((pointsForProfessor) => { const color = colorForProfessor(pointsForProfessor[0]?.instructor || ""); const path = pointsForProfessor.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${x(point.order)},${y(point.gpa)}`).join(" "); return `<path style="stroke:${color}" d="${path}"/>${pointsForProfessor.map((point) => `<circle class="trend-point-visual" style="stroke:${color}" cx="${x(point.order)}" cy="${y(point.gpa)}" r="4.5"/><circle class="trend-point trend-hit-area" cx="${x(point.order)}" cy="${y(point.gpa)}" r="4.5" data-term="${escapeHtml(`${point.semester_label} ${point.year}`)}" data-professor="${escapeHtml(point.instructor)}" data-gpa="${point.gpa.toFixed(2)}" data-enrollment="${Number(point.total_enrollment || 0).toLocaleString()}" data-sections="${point.section_count}"><title>${escapeHtml(`${point.instructor}: ${point.gpa.toFixed(2)} (${point.semester_label} ${point.year})`)}</title></circle>`).join("")}`; }).join("")}${terms.map(([order, label], index) => `<text class="x-label" x="${x(order)}" y="${height - 9}">${index % Math.ceil(terms.length / 5) === 0 ? escapeHtml(label.replace(" ", " '")) : ""}</text>`).join("")}</svg><div class="trend-tooltip" role="status"></div></div></div>`;
}

function courseTrendMarkup(history) {
  const seasonOrder = { Spring: 1, Summer: 2, Fall: 3 };
  const points = history
    .filter((term) => term.avg_gpa !== null && term.avg_gpa !== undefined)
    .map((term) => ({ ...term, gpa: Number(term.avg_gpa), order: Number(term.year) * 10 + (seasonOrder[term.semester_label] || 0) }))
    .sort((left, right) => left.order - right.order);
  if (!points.length) return '<p class="empty chart-empty">No course GPA history is available.</p>';
  const width = 760, height = 380, pad = { left: 48, right: 18, top: 32, bottom: 44 };
  const low = Math.min(...points.map((point) => point.gpa));
  const high = Math.max(...points.map((point) => point.gpa));
  const padding = Math.max(0.1, (high - low) * 0.15);
  let yLow = Math.max(0, Math.floor((low - padding) * 10) / 10);
  let yHigh = Math.min(4, Math.ceil((high + padding) * 10) / 10);
  if (yHigh - yLow < 0.4) { const midpoint = (low + high) / 2; yLow = Math.max(0, Math.floor((midpoint - 0.2) * 10) / 10); yHigh = Math.min(4, Math.ceil((midpoint + 0.2) * 10) / 10); }
  const axisValues = Array.from({ length: 5 }, (_, index) => yLow + ((yHigh - yLow) * index) / 4);
  const precision = yHigh - yLow < 1 ? 2 : 1;
  const xInset = 16;
  const x = (index) => pad.left + xInset + (index * ((width - pad.left - pad.right - xInset * 2) / Math.max(points.length - 1, 1)));
  const y = (gpa) => height - pad.bottom - ((gpa - yLow) / (yHigh - yLow)) * (height - pad.top - pad.bottom);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.gpa)}`).join(" ");
  return `<div class="professor-trend course-trend"><div class="chart-title"><span>Course GPA over time</span><small>All instructors and sections combined within each term</small></div><div class="trend-canvas"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Course GPA trend over time"><text class="y-axis-label" x="14" y="${height / 2}" transform="rotate(-90 14 ${height / 2})">GPA</text>${axisValues.map((value) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}"/><text class="y-axis-tick" x="${pad.left - 6}" y="${y(value) + 4}">${value.toFixed(precision)}</text>`).join("")}<path style="stroke:#74151d" d="${path}"/>${points.map((point, index) => `<circle class="trend-point-visual" style="stroke:#74151d" cx="${x(index)}" cy="${y(point.gpa)}" r="4.5"/><circle class="trend-point trend-hit-area" cx="${x(index)}" cy="${y(point.gpa)}" r="4.5" data-term="${escapeHtml(`${point.semester_label} ${point.year}`)}" data-gpa="${point.gpa.toFixed(2)}" data-enrollment="${Number(point.total_enrollment || 0).toLocaleString()}" data-sections="${Number(point.sections_observed || 0)}"><title>${escapeHtml(`${point.semester_label} ${point.year}: ${point.gpa.toFixed(2)}`)}</title></circle>`).join("")}${points.map((point, index) => `<text class="x-label" x="${x(index)}" y="${height - 9}">${index % Math.ceil(points.length / 5) === 0 ? escapeHtml(`${point.semester_label} '${String(point.year).slice(-2)}`) : ""}</text>`).join("")}</svg><div class="trend-tooltip" role="status"></div></div></div>`;
}

function enrollmentTrendMarkup(history) {
  const byYear = new Map();
  const currentYear = new Date().getFullYear();
  history.forEach((term) => {
    const enrollment = Number(term.total_enrollment);
    const year = Number(term.year);
    if (!Number.isFinite(enrollment) || !Number.isFinite(year) || year === currentYear) return;
    const annual = byYear.get(year) || { year, enrollment: 0, sections: 0 };
    annual.enrollment += enrollment;
    annual.sections += Number(term.sections_observed || 0);
    byYear.set(year, annual);
  });
  const points = [...byYear.values()].sort((left, right) => left.year - right.year);
  if (!points.length) return '<p class="empty chart-empty">No enrollment history is available.</p>';
  const width = 760, height = 380, pad = { left: 58, right: 18, top: 32, bottom: 44 };
  const high = Math.max(...points.map((point) => point.enrollment));
  const yHigh = Math.max(1, Math.ceil(high * 1.1));
  const axisValues = Array.from({ length: 5 }, (_, index) => (yHigh * index) / 4);
  const xInset = 16;
  const x = (index) => pad.left + xInset + (index * ((width - pad.left - pad.right - xInset * 2) / Math.max(points.length - 1, 1)));
  const y = (enrollment) => height - pad.bottom - (enrollment / yHigh) * (height - pad.top - pad.bottom);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.enrollment)}`).join(" ");
  const formatEnrollment = (value) => Math.round(value).toLocaleString();
  return `<div class="professor-trend course-trend enrollment-trend"><div class="chart-title"><span>Course enrollment by year</span><small>All reported Spring, Summer, and Fall sections combined</small></div><div class="trend-canvas"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Course enrollment by year"><text class="y-axis-label" x="14" y="${height / 2}" transform="rotate(-90 14 ${height / 2})">Enrolled</text>${axisValues.map((value) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}"/><text class="y-axis-tick" x="${pad.left - 6}" y="${y(value) + 4}">${formatEnrollment(value)}</text>`).join("")}<path style="stroke:#0f766e" d="${path}"/>${points.map((point, index) => `<circle class="trend-point-visual" style="stroke:#0f766e" cx="${x(index)}" cy="${y(point.enrollment)}" r="4.5"/><circle class="trend-point trend-hit-area" cx="${x(index)}" cy="${y(point.enrollment)}" r="4.5" data-metric="enrollment" data-term="${escapeHtml(String(point.year))}" data-enrollment="${formatEnrollment(point.enrollment)}" data-sections="${point.sections}"><title>${escapeHtml(`${point.year}: ${formatEnrollment(point.enrollment)} enrolled`)}</title></circle>`).join("")}${points.map((point, index) => `<text class="x-label" x="${x(index)}" y="${height - 9}">${escapeHtml(String(point.year))}</text>`).join("")}</svg><div class="trend-tooltip" role="status"></div></div></div>`;
}

function renderCourseRevamped(payload) {
  const course = payload.course;
  const prerequisites = String(course.prerequisites || "").trim();
  const loadedCurrentSections = (course.current_sections || []).filter(isCollegeStationSection);
  const currentTerm = String(payload.current_term_code || sectionTermCodes(loadedCurrentSections)[0] || "");
  const currentSections = loadedCurrentSections.filter(
    (section) => !currentTerm || String(section.term_code) === currentTerm,
  );
  const archivedSections = [
    ...loadedCurrentSections
      .filter((section) => currentTerm && String(section.term_code) !== currentTerm)
      .map((section) => ({ ...section, is_archived: true })),
    ...(course.archived_sections || []).filter(isCollegeStationSection),
  ];
  const allSections = [...currentSections, ...archivedSections];
  const currentTerms = currentTerm ? [currentTerm] : [];
  const offeringTerms = [...new Set([currentTerm, ...sectionTermCodes(allSections)].filter(Boolean))];
  const selectedTerm = currentTerm || offeringTerms[0] || "";
  const initial = selectedTerm ? allSections.filter((section) => String(section.term_code) === selectedTerm) : allSections;
  const trendOutcomes = payload.history.outcomes || [];
  const initialTrendInstructorIds = trendInstructorIds(initial, trendOutcomes);
  const initialTrendInstructorScope = selectedTerm || "all";
  const initialInstructorScope = selectedTerm && initialTrendInstructorIds.size ? selectedTerm : "all";
  const initialTrendInstructors = initialTrendInstructorScope === "all" ? null : initialTrendInstructorIds;
  const initiallySortedSections = [...initial].sort((left, right) => String(left.section || "").localeCompare(
    String(right.section || ""),
    undefined,
    { numeric: true },
  ));
  const history = payload.history.term_history || [];
  const semesterOrder = { Spring: 1, Summer: 2, Fall: 3 };
  const gradeReports = [...history].sort((a, b) => Number(b.year) - Number(a.year) || (semesterOrder[b.semester_label] || 0) - (semesterOrder[a.semester_label] || 0));
  const latest = gradeReports[0] || {};
  const tags = renderCourseTags({ ...course, current_sections: currentSections });
  const courseProfessorSummaries = payload.history.course_professor_summaries || [];
  const gpa = latest.avg_gpa == null ? "--" : Number(latest.avg_gpa).toFixed(2);
  const enrollment = latest.total_enrollment ? Number(latest.total_enrollment).toLocaleString() : "--";
  const overviewTone = gpaTone(latest.avg_gpa);
  updatePageMetadata(
    `${course.course_code} - ${course.title} | Aggie Courses`,
    `Review ${course.course_code} ${course.title} at Texas A&M, including requirements, sections, grade distributions, enrollment trends, and professor information.`,
  );
  pageElement.innerHTML = `
    <section class="course-hero course-overview">
      <div class="course-overview-main">
        <h1 class="course-heading"><span class="course-code"${course.subject_context ? ` title="${escapeHtml(course.subject_context)}"` : ""}>${escapeHtml(course.course_code)}</span><span>${escapeHtml(course.title)}</span>${course.credit_hours ? `<span class="course-credits">${escapeHtml(course.credit_hours)} credit${Number(course.credit_hours) === 1 ? "" : "s"}</span>` : ""}</h1>
        <p class="course-description">${courseReferenceMarkup(course.description || "No description available.")}</p>
        ${prerequisites ? `<div class="prerequisite-note"><strong>Prerequisites</strong><span>${courseReferenceMarkup(prerequisites)}</span></div>` : ""}
        ${course.cross_listings ? `<div class="prerequisite-note"><strong>Cross listings</strong><span>${courseReferenceMarkup(course.cross_listings)}</span></div>` : ""}
        ${course.unlocks?.length ? `<div class="unlocks-note"><strong>Required for</strong><span>${unlockedCoursesMarkup(course.unlocks)}</span></div>` : ""}
        ${apEquivalencyMarkup(course.ap_equivalencies)}
        ${tags}
      </div>
      <aside id="course-summary" class="course-summary metric-${overviewTone}"><div class="course-quick-stats">${sectionAvailabilityMarkup(initial)}</div><div class="summary-course-data"><div class="summary-report-navigation"><span id="summary-report-label">${latest.year ? `Past ${escapeHtml(latest.semester_label)} ${escapeHtml(latest.year)} data` : "No grade report available"}</span><div><button id="older-grade-report" type="button" aria-label="Show older grade report">&lt;</button><button id="newer-grade-report" type="button" aria-label="Show newer grade report">&gt;</button></div></div><div class="summary-metric summary-gpa"><strong id="summary-gpa" class="summary-gpa-value tone-${overviewTone}">${gpa}</strong><span>GPA</span></div><div class="summary-metric"><strong id="summary-enrollment">${enrollment}</strong><span>Enrolled</span></div></div></aside>
    </section>
    <section class="course-tabs" aria-label="Course details"><div class="course-tab-list" role="tablist"><button type="button" role="tab" id="tab-sections" aria-selected="true" aria-controls="panel-sections" data-course-tab="sections">Sections</button><button type="button" role="tab" id="tab-performance" aria-selected="false" aria-controls="panel-performance" data-course-tab="performance">Trends</button><button type="button" role="tab" id="tab-instructors" aria-selected="false" aria-controls="panel-instructors" data-course-tab="instructors">Instructors</button></div>
    <section id="panel-sections" class="course-detail-section sections-panel course-tab-panel" role="tabpanel" aria-labelledby="tab-sections">
      <div class="detail-section-heading"><div><p class="eyebrow">Plan your schedule</p><h2 id="section-heading">${selectedTerm ? escapeHtml(offeringLabel(selectedTerm, !currentTerms.includes(selectedTerm))) : "Current"} sections</h2></div><div class="section-controls"><label class="section-term-filter">Semester<select id="section-term-filter">${offeringTerms.length > 1 ? '<option value="">All loaded terms</option>' : ""}${offeringTerms.map((term) => `<option value="${escapeHtml(term)}" ${term === selectedTerm ? "selected" : ""}>${escapeHtml(offeringLabel(term, !currentTerms.includes(term)))}</option>`).join("")}</select></label><label class="open-sections-filter"><input id="open-sections-filter" type="checkbox" /> Open only</label></div></div>
      <div id="section-pager-row" class="section-pager-row ${initial.length > 8 ? "has-pager" : ""}"><p id="section-count" class="section-count">Showing ${Math.min(initial.length, 8)} of ${initial.length} sections</p><div id="show-more-sections" class="show-more-sections">${sectionPagerMarkup(0, initial.length)}</div></div><div id="current-sections-table">${detailedSectionsMarkup(initiallySortedSections.slice(0, 8), null, 1)}</div>
    </section>
    <section id="panel-performance" class="course-detail-section course-tab-panel" role="tabpanel" aria-labelledby="tab-performance" hidden><div class="detail-section-heading"><div><p class="eyebrow">Historical outcomes</p><h2>Course trends by term</h2></div><p>${payload.history.start_year} onward</p></div><div class="chart-filter-panel"><div><strong>Trend to chart</strong><small>Compare instructors or view the course as one combined line.</small></div><div id="course-metric-toggle" class="course-metric-toggle" role="radiogroup" aria-label="Course trend metric"><label class="chart-term-choice"><input type="radio" name="course-metric" value="instructors" checked /> Instructors <select id="chart-instructor-scope" aria-label="Instructor scope"><option value="all" ${initialTrendInstructorScope === "all" ? "selected" : ""}>All instructors</option>${offeringTerms.map((term) => `<option value="${escapeHtml(term)}" ${term === initialTrendInstructorScope ? "selected" : ""}>${escapeHtml(offeringLabel(term, !currentTerms.includes(term)))}</option>`).join("")}</select></label><label class="chart-term-choice"><input type="radio" name="course-metric" value="gpa" /> Course GPA</label><label class="chart-term-choice"><input type="radio" name="course-metric" value="enrollment" /> Course Enrollment</label></div><span id="chart-selected-instructor"></span></div><div id="professor-trend-chart">${initialTrendInstructorIds.size ? professorTrendMarkup(trendOutcomes, initialTrendInstructors) : `<p class="empty chart-empty">No grade-report is available for instructors teaching in ${escapeHtml(offeringLabel(selectedTerm, false))}.</p>`}</div></section>
    <section id="panel-instructors" class="course-detail-section course-tab-panel" role="tabpanel" aria-labelledby="tab-instructors" hidden><div class="detail-section-heading"><div><p class="eyebrow">Instructors</p><h2>Overall GPA by professor</h2></div><p>For this course, ${payload.history.start_year} onward</p></div><div class="chart-filter-panel instructor-filter-panel"><div><strong>Instructors to show</strong><small>Limit the table to an offering or include everyone who has taught this course.</small></div><label class="chart-term-choice">Instructors <select id="instructor-table-scope"><option value="all" ${initialInstructorScope === "all" ? "selected" : ""}>All instructors</option>${offeringTerms.map((term) => `<option value="${escapeHtml(term)}" ${term === initialInstructorScope ? "selected" : ""}>${escapeHtml(offeringLabel(term, !currentTerms.includes(term)))}</option>`).join("")}</select></label></div><div id="professor-summary-table">${professorSummaryMarkup(courseProfessorSummaries)}</div></section></section>`;
  const outcomesByTerm = new Map();
  (payload.history.outcomes || []).forEach((outcome) => {
    const key = `${outcome.year}-${outcome.semester}`;
    const group = outcomesByTerm.get(key) || [];
    group.push(outcome);
    outcomesByTerm.set(key, group);
  });
  const termRows = [...(payload.history.term_history || [])].sort((left, right) => {
    const semesterOrder = { Spring: 1, Summer: 2, Fall: 3 };
    return Number(right.year) - Number(left.year)
      || (semesterOrder[right.semester_label] || 0) - (semesterOrder[left.semester_label] || 0);
  }).map((term) => {
    const outcomes = outcomesByTerm.get(`${term.year}-${term.semester}`) || [];
    const detailId = `course-term-${term.year}-${term.semester}`;
    return `<tr><td>${escapeHtml(`${term.semester_label} ${term.year}`)}</td><td>${term.avg_gpa == null ? "—" : Number(term.avg_gpa).toFixed(2)}</td><td>${Number(term.total_enrollment || 0).toLocaleString()}</td><td>${Number(term.sections_observed || 0).toLocaleString()}${outcomes.length ? `<button class="section-detail-toggle" type="button" data-detail-id="${detailId}" aria-expanded="false">View sections</button>` : ""}</td></tr>${outcomes.length ? `<tr id="${detailId}" class="section-detail-row" hidden><td colspan="4">${termOutcomeDetailsMarkup(outcomes)}</td></tr>` : ""}`;
  }).join("");
  const performancePanel = document.querySelector("#panel-performance");
  performancePanel?.insertAdjacentHTML("beforeend", `<div class="outcome-history-panel"><h3>Term-by-term section details</h3><p>View each section's total enrollment and grade counts.</p><div class="table-scroll"><table class="detail-table"><thead><tr><th>Term</th><th>GPA</th><th>Total</th><th>Sections</th></tr></thead><tbody>${termRows}</tbody></table></div></div>`);
  performancePanel?.addEventListener("click", (event) => {
    const button = event.target.closest(".section-detail-toggle");
    if (!button) return;
    const detailRow = document.querySelector(`#${button.dataset.detailId}`);
    if (!detailRow) return;
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    button.textContent = expanded ? "View sections" : "Hide sections";
    detailRow.hidden = expanded;
  });

  const termFilter = document.querySelector("#section-term-filter");
  const openFilter = document.querySelector("#open-sections-filter");
  const table = document.querySelector("#current-sections-table");
  const count = document.querySelector("#section-count");
  const sectionHeading = document.querySelector("#section-heading");
  const showMore = document.querySelector("#show-more-sections");
  const pagerRow = document.querySelector("#section-pager-row");
  const courseSummary = document.querySelector("#course-summary");
  const reportLabel = document.querySelector("#summary-report-label");
  const summaryGpa = document.querySelector("#summary-gpa");
  const summaryEnrollment = document.querySelector("#summary-enrollment");
  const olderGradeReport = document.querySelector("#older-grade-report");
  const newerGradeReport = document.querySelector("#newer-grade-report");
  let gradeReportIndex = 0;
  const renderGradeReport = () => {
    const report = gradeReports[gradeReportIndex] || {};
    const label = report.year ? `${report.semester_label} ${report.year}` : "No grade report available";
    const tone = gpaTone(report.avg_gpa);
    courseSummary.className = `course-summary metric-${tone}`;
    reportLabel.textContent = report.year ? `Past ${label} data` : label;
    summaryGpa.textContent = report.avg_gpa == null ? "--" : Number(report.avg_gpa).toFixed(2);
    summaryGpa.className = `summary-gpa-value tone-${tone}`;
    summaryEnrollment.textContent = report.total_enrollment ? Number(report.total_enrollment).toLocaleString() : "--";
    olderGradeReport.disabled = gradeReportIndex >= gradeReports.length - 1;
    newerGradeReport.disabled = gradeReportIndex === 0;
  };
  olderGradeReport.addEventListener("click", () => { gradeReportIndex += 1; renderGradeReport(); });
  newerGradeReport.addEventListener("click", () => { gradeReportIndex -= 1; renderGradeReport(); });
  renderGradeReport();
  const unlocksToggle = document.querySelector(".unlocks-toggle");
  unlocksToggle?.addEventListener("click", () => {
    const list = unlocksToggle.closest(".unlocks-courses");
    const expanded = list?.classList.toggle("expanded");
    unlocksToggle.textContent = expanded ? "Show less" : `+${unlocksToggle.dataset.remaining} more`;
    unlocksToggle.setAttribute("aria-expanded", String(Boolean(expanded)));
  });
  const attributeToggles = document.querySelectorAll(".tag-more-toggle");
  attributeToggles.forEach((toggle) => {
    toggle.addEventListener("click", () => {
      const tags = toggle.closest(".course-tags");
      const expanded = toggle.dataset.action === "expand";
      tags?.classList.toggle("expanded", expanded);
      attributeToggles.forEach((button) => button.setAttribute("aria-expanded", String(expanded)));
    });
  });
  let sectionPage = 0;
  let sectionGpaSortDirection = null;
  let sectionNumberSortDirection = 1;
  const sectionGpa = (section) => {
    const value = (section.professor_gpas || [])[0]?.avg_gpa;
    return value === null || value === undefined || value === "" ? Number.NaN : Number(value);
  };
  const updateSections = () => {
    let sections = termFilter.value ? allSections.filter((section) => String(section.term_code) === termFilter.value) : allSections;
    if (openFilter.checked) sections = sections.filter((section) => !section.is_archived && String(section.seat_status_open || "").toUpperCase() === "Y");
    sectionHeading.textContent = termFilter.value
      ? `${offeringLabel(termFilter.value, !currentTerms.includes(termFilter.value))} sections`
      : "All loaded College Station sections";
    if (sectionNumberSortDirection) {
      sections = [...sections].sort((left, right) => String(left.section || "").localeCompare(
        String(right.section || ""),
        undefined,
        { numeric: true },
      ) * sectionNumberSortDirection);
    } else if (sectionGpaSortDirection) {
      const sectionsWithGpa = sections.filter((section) => Number.isFinite(sectionGpa(section)));
      const sectionsWithoutGpa = sections.filter((section) => !Number.isFinite(sectionGpa(section)));
      const sortedSectionsWithGpa = sectionsWithGpa.sort(
        (left, right) => (sectionGpa(left) - sectionGpa(right)) * sectionGpaSortDirection,
      );
      sections = sectionGpaSortDirection === 1
        ? [...sectionsWithoutGpa, ...sortedSectionsWithGpa]
        : [...sortedSectionsWithGpa, ...sectionsWithoutGpa];
    }
    const pageStart = sectionPage * 8;
    if (pageStart >= sections.length) sectionPage = 0;
    const visible = sections.slice(sectionPage * 8, sectionPage * 8 + 8);
    table.innerHTML = detailedSectionsMarkup(visible, sectionGpaSortDirection, sectionNumberSortDirection);
    table.scrollTop = 0;
    count.textContent = sections.length
      ? `Showing ${sectionPage * 8 + 1}-${sectionPage * 8 + visible.length} of ${sections.length} sections`
      : "Showing 0 of 0 sections";
    showMore.innerHTML = sectionPagerMarkup(sectionPage, sections.length);
    pagerRow.classList.toggle("has-pager", sections.length > 8);
  };
  termFilter.addEventListener("change", () => { sectionPage = 0; updateSections(); });
  openFilter.addEventListener("change", () => { sectionPage = 0; updateSections(); });
  showMore.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-page-index]");
    if (!button) return;
    sectionPage = Number(button.dataset.pageIndex);
    updateSections();
  });
  table.addEventListener("click", (event) => {
    const gpaSortButton = event.target.closest(".section-gpa-sort");
    const sectionSortButton = event.target.closest(".section-number-sort");
    if (!gpaSortButton && !sectionSortButton) return;
    if (gpaSortButton) {
      sectionGpaSortDirection = gpaSortButton.getAttribute("aria-sort") === "descending" ? 1 : -1;
      sectionNumberSortDirection = null;
    } else {
      sectionNumberSortDirection = sectionNumberSortDirection === -1 ? 1 : -1;
      sectionGpaSortDirection = null;
    }
    sectionPage = 0;
    updateSections();
  });
  const trendCanvas = document.querySelector("#professor-trend-chart");
  const trendTooltip = document.querySelector(".trend-tooltip");
  const chartInstructorScope = document.querySelector("#chart-instructor-scope");
  const trendTarget = document.querySelector("#professor-trend-chart");
  const selectedInstructorLabel = document.querySelector("#chart-selected-instructor");
  const courseMetricToggle = document.querySelector("#course-metric-toggle");
  trendTarget.querySelectorAll("title").forEach((title) => title.remove());
  let selectedProfessor = null;
  let selectedCourseMetric = "instructors";
  const renderTrend = () => {
    if (selectedCourseMetric === "gpa" || selectedCourseMetric === "enrollment") {
      trendTarget.innerHTML = selectedCourseMetric === "enrollment" ? enrollmentTrendMarkup(history) : courseTrendMarkup(history);
      trendTarget.querySelectorAll("title").forEach((title) => title.remove());
      chartInstructorScope.disabled = true;
      selectedInstructorLabel.innerHTML = "";
      courseMetricToggle.querySelector(`[value="${selectedCourseMetric}"]`).checked = true;
      return;
    }
    const scope = chartInstructorScope.value;
    let instructors = null;
    if (scope !== "all") {
      instructors = trendInstructorIds(
        allSections.filter((section) => String(section.term_code) === scope),
        trendOutcomes,
      );
      if (!instructors.size) {
        trendTarget.innerHTML = `<p class="empty chart-empty">No grade-report is available for instructors teaching in ${escapeHtml(offeringLabel(scope, false))}.</p>`;
        selectedInstructorLabel.innerHTML = "";
        chartInstructorScope.disabled = false;
        courseMetricToggle.querySelector('[value="instructors"]').checked = true;
        return;
      }
    }
    trendTarget.innerHTML = professorTrendMarkup(trendOutcomes, instructors, selectedProfessor);
    trendTarget.querySelectorAll("title").forEach((title) => title.remove());
    chartInstructorScope.disabled = false;
    selectedInstructorLabel.innerHTML = selectedProfessor ? `<button type="button" class="clear-chart-selection" title="Show all selected instructors">${escapeHtml(selectedProfessor)} ×</button>` : "";
    courseMetricToggle.querySelector('[value="instructors"]').checked = true;
  };
  chartInstructorScope.addEventListener("change", () => { selectedCourseMetric = "instructors"; selectedProfessor = null; renderTrend(); });
  courseMetricToggle.addEventListener("change", (event) => {
    const option = event.target.closest('input[name="course-metric"]');
    if (!option || option.value === selectedCourseMetric) return;
    selectedCourseMetric = option.value;
    selectedProfessor = null;
    renderTrend();
  });
  selectedInstructorLabel.addEventListener("click", () => { selectedProfessor = null; renderTrend(); });
  trendCanvas?.addEventListener("pointerover", (event) => {
    const point = event.target.closest(".trend-point");
    const chartCanvas = point?.closest(".trend-canvas");
    const tooltip = chartCanvas?.querySelector(".trend-tooltip");
    if (!point || !tooltip || !chartCanvas) return;
    if (point.dataset.metric === "enrollment") {
      tooltip.innerHTML = `<strong>${escapeHtml(point.dataset.term)}</strong><span>${escapeHtml(point.dataset.sections)} section${point.dataset.sections === "1" ? "" : "s"}</span><b>${escapeHtml(point.dataset.enrollment)} enrolled</b>`;
      const pointBounds = point.getBoundingClientRect();
      const canvasBounds = chartCanvas.getBoundingClientRect();
      tooltip.style.left = `${pointBounds.left - canvasBounds.left + pointBounds.width / 2}px`;
      tooltip.style.top = `${pointBounds.top - canvasBounds.top}px`;
      tooltip.classList.add("visible");
      return;
    }
    const combined = !point.dataset.professor;
    tooltip.innerHTML = combined
      ? `<strong>${escapeHtml(point.dataset.term)}</strong><span>${escapeHtml(point.dataset.enrollment)} enrolled · ${escapeHtml(point.dataset.sections)} section${point.dataset.sections === "1" ? "" : "s"}</span><b>GPA ${escapeHtml(point.dataset.gpa)}</b>`
      : `<strong>${escapeHtml(point.dataset.professor)}</strong><span>${escapeHtml(point.dataset.term)} · ${escapeHtml(point.dataset.sections)} section${point.dataset.sections === "1" ? "" : "s"}</span><b>GPA ${escapeHtml(point.dataset.gpa)}</b>`;
    if (!combined) {
      const instructorSummary = [
        point.dataset.term,
        `${point.dataset.enrollment} enrolled`,
        `${point.dataset.sections} section${point.dataset.sections === "1" ? "" : "s"}`,
      ].join(" | ");
      tooltip.innerHTML = `<strong>${escapeHtml(point.dataset.professor)}</strong><span>${escapeHtml(instructorSummary)}</span><b>GPA ${escapeHtml(point.dataset.gpa)}</b>`;
    }
    const pointBounds = point.getBoundingClientRect();
    const canvasBounds = chartCanvas.getBoundingClientRect();
    tooltip.style.left = `${pointBounds.left - canvasBounds.left + pointBounds.width / 2}px`;
    tooltip.style.top = `${pointBounds.top - canvasBounds.top}px`;
    tooltip.classList.add("visible");
  });
  trendCanvas?.addEventListener("pointerout", (event) => {
    if (event.target.closest(".trend-point")) event.target.closest(".trend-canvas")?.querySelector(".trend-tooltip")?.classList.remove("visible");
  });
  trendCanvas?.addEventListener("click", (event) => {
    const legendProfessor = event.target.closest(".trend-legend-professor");
    if (legendProfessor) {
      const professor = legendProfessor.dataset.professor;
      selectedProfessor = selectedProfessor === professor ? null : professor;
      renderTrend();
      return;
    }
    const point = event.target.closest(".trend-point");
    if (!point || !point.dataset.professor) return;
    selectedProfessor = selectedProfessor === point.dataset.professor ? null : point.dataset.professor;
    renderTrend();
  });
  trendCanvas?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const legendProfessor = event.target.closest(".trend-legend-professor");
    if (!legendProfessor) return;
    event.preventDefault();
    const professor = legendProfessor.dataset.professor;
    selectedProfessor = selectedProfessor === professor ? null : professor;
    renderTrend();
  });
  document.querySelector(".course-tab-list")?.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-course-tab]");
    if (!tab) return;
    const target = tab.dataset.courseTab;
    document.querySelectorAll("[data-course-tab]").forEach((button) => button.setAttribute("aria-selected", String(button === tab)));
    document.querySelectorAll(".course-tab-panel").forEach((panel) => { panel.hidden = panel.id !== `panel-${target}`; });
  });
  const professorSummaryRowsElement = document.querySelector("#professor-summary-rows");
  if (!professorSummaryRowsElement) return;
  const instructorTableScope = document.querySelector("#instructor-table-scope");
  let professorSortKey = "professor";
  let professorSortDirection = 1;
  const displayedProfessorSummaries = () => {
    const scope = instructorTableScope.value;
    if (scope === "all") return courseProfessorSummaries;
    const instructorIds = new Set(allSections.filter((section) => String(section.term_code) === scope).flatMap((section) => (section.normalized_instructors || []).map((instructor) => instructor.instructor_id)).filter(Boolean));
    return courseProfessorSummaries.filter((summary) => instructorIds.has(summary.professor_key));
  };
  const renderProfessorSummaries = () => {
    const visibleSummaries = displayedProfessorSummaries();
    const sorted = [...visibleSummaries].sort((left, right) => {
      if (!professorSortKey) return 0;
      if (professorSortKey === "professor") {
        return String(left.professor || "").localeCompare(String(right.professor || "")) * professorSortDirection;
      }
      const numericValue = (summary) => {
        const value = professorSortKey === "confidence_gpa"
          ? confidenceGpa(summary, visibleSummaries)
          : summary[professorSortKey];
        return value === null || value === undefined || value === "" ? Number.NaN : Number(value);
      };
      const leftValue = numericValue(left);
      const rightValue = numericValue(right);
      if (!Number.isFinite(leftValue) && !Number.isFinite(rightValue)) return 0;
      if (!Number.isFinite(leftValue)) return professorSortDirection === 1 ? -1 : 1;
      if (!Number.isFinite(rightValue)) return professorSortDirection === 1 ? 1 : -1;
      const comparison = leftValue - rightValue;
      return comparison * professorSortDirection;
    });
    professorSummaryRowsElement.innerHTML = professorSummaryRows(sorted, visibleSummaries) || '<tr><td colspan="6">No instructors found for this offering.</td></tr>';
  };
  document.querySelectorAll(".professor-sort").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.professorSort;
      professorSortDirection = professorSortKey === key ? professorSortDirection * -1 : -1;
      professorSortKey = key;
      renderProfessorSummaries();
      document.querySelectorAll(".professor-sort").forEach((control) => {
        const active = control === button;
        control.setAttribute("aria-sort", active ? (professorSortDirection === 1 ? "ascending" : "descending") : "none");
        control.querySelector("span").textContent = active ? (professorSortDirection === 1 ? "↑" : "↓") : "↕";
      });
    });
  });
  instructorTableScope.addEventListener("change", renderProfessorSummaries);
  renderProfessorSummaries();
}

async function loadCourse() {
  const courseIdentifier = window.location.pathname.split("/").filter(Boolean).pop();
  const from = internalPath(new URLSearchParams(window.location.search).get("from"));
  if (from) storeSessionPath(SEARCH_RETURN_STORAGE_KEY, from);
  const returnToSearch = from || readSessionPath(SEARCH_RETURN_STORAGE_KEY);
  if (returnToSearch) backLink.href = returnToSearch;
  removeLegacyFromParameter();
  try {
    const response = await fetch(`/api/courses/${encodeURIComponent(courseIdentifier)}`);
    if (!response.ok) throw new Error(response.status === 404 ? "This course could not be found." : `Unable to load course details (${response.status}).`);
    renderCourseRevamped(await response.json());
  } catch (error) {
    pageElement.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

pageElement.addEventListener("click", (event) => {
  const professorLink = event.target.closest('a[href^="/professor/"]');
  if (!professorLink) return;
  storeSessionPath(
    COURSE_RETURN_STORAGE_KEY,
    `${window.location.pathname}${window.location.search}${window.location.hash}`,
  );
});

loadCourse();
