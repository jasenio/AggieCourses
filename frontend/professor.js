const professorPage = document.querySelector("#professor-page");
const professorBackLink = document.querySelector("#back-to-course");
const SEARCH_RETURN_STORAGE_KEY = "crs:return-to-search";
const COURSE_RETURN_STORAGE_KEY = "crs:return-to-course";

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
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

function coursePathFromLegacyValue(value) {
  const path = internalPath(value);
  if (!path) return "";
  const url = new URL(path, window.location.origin);
  const searchReturn = internalPath(url.searchParams.get("from"));
  if (searchReturn) storeSessionPath(SEARCH_RETURN_STORAGE_KEY, searchReturn);
  url.searchParams.delete("from");
  return `${url.pathname}${url.search}${url.hash}`;
}

function removeLegacyFromParameter() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("from")) return;
  url.searchParams.delete("from");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function gpaTone(value) {
  if (value == null) return "neutral";
  if (Number(value) >= 3.35) return "green";
  if (Number(value) >= 2.75) return "gold";
  return "red";
}

function termLabel(entry) { return `${entry.semester_label} ${entry.year}`; }

function sectionTermLabel(termCode) {
  const value = String(termCode || "");
  const season = { 1: "Spring", 2: "Summer", 3: "Fall" }[value[4]];
  return season && value.length >= 4 ? `${season} ${value.slice(0, 4)}` : value;
}

function collegeStationOfferingLabel(termCode, isArchived = false) {
  return `${sectionTermLabel(termCode)} - College Station${isArchived ? " (archived)" : ""}`;
}

function isCollegeStationSection(section) {
  return (section.filter_locations || []).some((location) => String(location).toLowerCase() === "college station")
    || String(section.site || "").toLowerCase() === "college station";
}

function courseLink(course) {
  return `<a class="course-reference-link" href="/course/${encodeURIComponent(course.course_id)}">${escapeHtml(course.course_code)}</a>`;
}

function statusMarkup(section, currentOfferingTerm = "") {
  if (currentOfferingTerm && String(section.term_code || "") < currentOfferingTerm) {
    return '<span class="section-status archived">Archived</span>';
  }
  const status = String(section.seat_status_open || "").toUpperCase();
  if (status === "Y") return '<span class="section-status open">Open</span>';
  if (status === "N") return '<span class="section-status closed">Closed</span>';
  return '<span class="section-status unavailable">N/A</span>';
}

function splitScheduleValues(value) {
  return String(value || "").split(/\s*(?:;|\||\n)\s*/).map((item) => item.trim()).filter(Boolean);
}

function compactSectionFormat(instructionType) {
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
  return instructionType || "--";
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
  if (!count) return "--";
  const lines = [];
  for (let index = 0; index < count; index += 1) {
    const rawDays = dayGroups[index] || dayGroups[0] || "TBA";
    const namedDays = rawDays.match(/Mon|Tue|Wed|Thu|Fri|Sat|Sun/gi);
    const days = namedDays
      ? namedDays.map((day) => `${day.slice(0, 1).toUpperCase()}${day.slice(1).toLowerCase()}`)
      : (/^[MTWRFSU]+$/i.test(rawDays) ? [...rawDays.toUpperCase()].map((day) => weekdayNames[day]).filter(Boolean) : [rawDays]);
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
      if (!match[3]) return null;
      const category = match[2].replace(/^the following\s+/i, "").replace(/\s+$/, "").replace(/s$/i, "").replace(/^Student Attribute$/i, "Student attribute");
      return { item, category, label: category, value: `${/^(May not|Cannot)/i.test(match[1]) ? "May not be" : "Must be"}: ${match[3]}` };
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
  rawItems.forEach(({ category }) => { if (!groupLabels.has(category.toLowerCase()) && !categories.includes(category)) categories.push(category); });
  const detailRows = groups.map(([label, included, excluded]) => `<div><strong>${escapeHtml(label)}</strong><span>${escapeHtml([included.length ? `Must be: ${included.join(", ")}` : "", excluded.length ? `May not be: ${excluded.join(", ")}` : ""].filter(Boolean).join(" · "))}</span></div>`).join("");
  const rawRows = rawItems.filter(({ category }) => !groupLabels.has(category.toLowerCase())).map(({ item, category, label, value }) => { const match = item.match(/^\s*([^:]+):\s*(.+)$/); return `<div><strong>${escapeHtml(label || (match ? match[1] : category))}</strong><span>${escapeHtml(value || (match ? match[2] : item))}</span></div>`; }).join("");
  return `<details class="section-restrictions"><summary><span class="restriction-chip">${escapeHtml(categories.join(" · ") || "Registration")}</span></summary><div class="restriction-breakdown">${detailRows}${rawRows}</div></details>`;
}

function detailedSectionsMarkup(sections, currentOfferingTerm) {
  if (!sections.length) return '<p class="empty">No sections match these filters.</p>';
  return `<div class="table-scroll"><table class="detail-table current-sections-table restored-sections-table"><thead><tr><th>Status</th><th>Course</th><th>Section / CRN</th><th>Section title</th><th>Format / campus</th><th>Meeting</th><th>Restrictions</th></tr></thead><tbody>${sections.map((section) => `<tr><td>${statusMarkup(section, currentOfferingTerm)}</td><td><strong>${courseLink(section)}</strong><small>${escapeHtml(section.course_title || "")}</small></td><td><strong>${escapeHtml(section.section || "--")}</strong><small>CRN ${escapeHtml(section.crn || "--")}</small></td><td>${escapeHtml(section.title || "--")}</td><td>${escapeHtml(compactSectionFormat(section.instruction_type))}<small>${escapeHtml(section.site || "--")}</small></td><td>${meetingMarkup(section)}</td><td class="restriction-cell">${restrictionDetailsMarkup(section)}</td></tr>`).join("")}</tbody></table></div>`;
}

function courseTrendMarkup(outcomes, courses, selectedCourse = null) {
  const seasonOrder = { Spring: 1, Summer: 2, Fall: 3 };
  const colors = ["#d7191c", "#0072b2", "#7b2cbf", "#e87500", "#008f5a", "#d81b60", "#8a7800", "#00a6d6"];
  const courseNames = new Map(courses.map((course) => [course.course_id, course.course_code]));
  const aggregates = new Map();
  outcomes.filter((outcome) => Number(outcome.gpa_weight) > 0).forEach((outcome) => {
    const order = Number(outcome.year) * 10 + (seasonOrder[outcome.semester_label] || 0);
    const key = `${outcome.course_id}::${order}`;
    const entry = aggregates.get(key) || { course_id: outcome.course_id, course: courseNames.get(outcome.course_id) || String(outcome.course_id || "").replace("-", " "), order, term: termLabel(outcome), grade_points_total: 0, gpa_weight: 0, total_enrollment: 0, section_count: 0 };
    entry.grade_points_total += Number(outcome.grade_points_total || 0);
    entry.gpa_weight += Number(outcome.gpa_weight || 0);
    entry.total_enrollment += Number(outcome.total_enrollment || 0);
    entry.section_count += 1;
    entry.gpa = entry.grade_points_total / entry.gpa_weight;
    aggregates.set(key, entry);
  });
  const allPoints = [...aggregates.values()].sort((left, right) => left.order - right.order);
  const shownPoints = selectedCourse ? allPoints.filter((point) => point.course_id === selectedCourse) : allPoints;
  if (!shownPoints.length) return '<p class="empty chart-empty">No grade-report history is available for the selected course.</p>';
  const courseIds = [...new Set(allPoints.map((point) => point.course_id))].sort((left, right) => String(courseNames.get(left) || left).localeCompare(String(courseNames.get(right) || right)));
  const terms = [...new Map(allPoints.map((point) => [point.order, point.term])).entries()];
  const width = 760, height = 380, pad = { left: 48, right: 18, top: 32, bottom: 44 };
  const values = allPoints.map((point) => point.gpa);
  const padding = Math.max(.1, (Math.max(...values) - Math.min(...values)) * .15);
  let low = Math.max(0, Math.floor((Math.min(...values) - padding) * 10) / 10);
  let high = Math.min(4, Math.ceil((Math.max(...values) + padding) * 10) / 10);
  if (high - low < .4) { const middle = (high + low) / 2; low = Math.max(0, middle - .2); high = Math.min(4, middle + .2); }
  const x = (order) => pad.left + 16 + terms.findIndex(([term]) => term === order) * ((width - pad.left - pad.right - 32) / Math.max(terms.length - 1, 1));
  const y = (gpa) => height - pad.bottom - ((gpa - low) / (high - low)) * (height - pad.top - pad.bottom);
  const ticks = Array.from({ length: 5 }, (_, index) => low + ((high - low) * index) / 4);
  const series = courseIds.filter((courseId) => !selectedCourse || courseId === selectedCourse).map((courseId) => shownPoints.filter((point) => point.course_id === courseId));
  return `<div class="professor-trend"><div class="chart-title"><span>Course GPA over time</span><small>All sections combined within each term</small></div><div class="trend-legend">${courseIds.map((courseId, index) => `<span class="trend-legend-professor" role="button" tabindex="0" data-course-id="${escapeHtml(courseId)}" aria-pressed="${String(selectedCourse === courseId)}"><i style="background:${colors[index % colors.length]}\"></i>${escapeHtml(courseNames.get(courseId) || courseId)}</span>`).join("")}</div><div class="trend-canvas"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Course GPA trends over time"><text class="y-axis-label" x="14" y="${height / 2}" transform="rotate(-90 14 ${height / 2})">GPA</text>${ticks.map((value) => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}"/><text class="y-axis-tick" x="${pad.left - 6}" y="${y(value) + 4}">${value.toFixed(1)}</text>`).join("")}${series.map((points) => { const color = colors[courseIds.indexOf(points[0].course_id) % colors.length]; const path = points.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${x(point.order)},${y(point.gpa)}`).join(" "); return `<path style="stroke:${color}" d="${path}"/>${points.map((point) => `<circle class="trend-point-visual" style="stroke:${color}" cx="${x(point.order)}" cy="${y(point.gpa)}" r="4.5"/><circle class="trend-point trend-hit-area" cx="${x(point.order)}" cy="${y(point.gpa)}" r="4.5" data-course-id="${escapeHtml(point.course_id)}" data-course="${escapeHtml(point.course)}" data-term="${escapeHtml(point.term)}" data-gpa="${point.gpa.toFixed(2)}" data-enrollment="${point.total_enrollment}" data-sections="${point.section_count}"></circle>`).join("")}`; }).join("")}${terms.map(([order, label], index) => `<text class="x-label" x="${x(order)}" y="${height - 9}">${index % Math.ceil(terms.length / 5) === 0 ? escapeHtml(label.replace(" ", " '")) : ""}</text>`).join("")}</svg><div class="trend-tooltip" role="status"></div></div></div>`;
}

const GRADE_LABELS = [["a_plus", "A+"], ["a", "A"], ["a_minus", "A−"], ["b_plus", "B+"], ["b", "B"], ["b_minus", "B−"], ["c_plus", "C+"], ["c", "C"], ["c_minus", "C−"], ["d_plus", "D+"], ["d", "D"], ["d_minus", "D−"], ["f", "F"], ["q", "Q drops"], ["i", "I"], ["s", "S"], ["p", "P"], ["u", "U"], ["x", "X"]];

function outcomeDetailsMarkup(outcomes, courses) {
  const courseNames = new Map(courses.map((course) => [course.course_id, course.course_code]));
  const fields = [["course", "Course"], ["section", "Section"], ["total", "Total"], ["gpa", "GPA"], ["a", "A"], ["b", "B"], ["c", "C"], ["d", "D"], ["f", "F"], ["q", "Q"]];
  return `<div class="section-grade-details professor-section-grade-details"><div class="section-grade-head">${fields.map(([, label]) => `<span>${label}</span>`).join("")}</div>${outcomes.map((outcome) => { const counts = outcome.grade_counts || {}; const values = { course: escapeHtml(courseNames.get(outcome.course_id) || String(outcome.course_id || "").replace("-", " ")), section: escapeHtml(outcome.section || "—"), total: Number(outcome.total_enrollment || 0).toLocaleString(), gpa: outcome.gpa_weight ? (Number(outcome.grade_points_total || 0) / Number(outcome.gpa_weight)).toFixed(2) : "—", a: Number(counts.a || 0).toLocaleString(), b: Number(counts.b || 0).toLocaleString(), c: Number(counts.c || 0).toLocaleString(), d: Number(counts.d || 0).toLocaleString(), f: Number(counts.f || 0).toLocaleString(), q: Number(counts.q || 0).toLocaleString() }; return `<div class="section-grade-row">${fields.map(([key, label]) => `<span><b>${label}</b>${values[key]}</span>`).join("")}</div>`; }).join("")}</div>`;
}

function termOutcomeHistoryMarkup(outcomes, courses) {
  const groups = new Map();
  outcomes.forEach((outcome) => { const key = `${outcome.year}-${outcome.semester}`; const group = groups.get(key) || { label: termLabel(outcome), outcomes: [] }; group.outcomes.push(outcome); groups.set(key, group); });
  const rows = [...groups.entries()].map(([key, group]) => { const enrollment = group.outcomes.reduce((sum, outcome) => sum + Number(outcome.total_enrollment || 0), 0); const graded = group.outcomes.reduce((sum, outcome) => sum + Number(outcome.gpa_weight || 0), 0); const points = group.outcomes.reduce((sum, outcome) => sum + Number(outcome.grade_points_total || 0), 0); const detailId = `professor-term-${key}`; return `<tr><td>${escapeHtml(group.label)}</td><td>${graded ? (points / graded).toFixed(2) : "—"}</td><td>${enrollment.toLocaleString()}</td><td>${group.outcomes.length.toLocaleString()}<button class="section-detail-toggle" type="button" data-detail-id="${detailId}" aria-expanded="false">View sections</button></td></tr><tr id="${detailId}" class="section-detail-row" hidden><td colspan="4">${outcomeDetailsMarkup(group.outcomes, courses)}</td></tr>`; }).join("");
  return rows ? `<div class="table-scroll"><table class="detail-table"><thead><tr><th>Term</th><th>GPA</th><th>Total</th><th>Sections</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No grade-report history is available.</p>';
}

const COURSE_PAGE_SIZE = 8;

function courseRowsMarkup(courses, key, direction, page = 0) {
  const value = (course) => key === "course_code" ? String(course.course_code || "") : Number(course[key] || 0);
  const sorted = [...courses].sort((left, right) => (key === "course_code" ? value(left).localeCompare(value(right)) : value(left) - value(right)) * direction);
  return sorted.slice(page * COURSE_PAGE_SIZE, page * COURSE_PAGE_SIZE + COURSE_PAGE_SIZE).map((course) => `<tr><td><strong>${courseLink(course)}</strong><small>${escapeHtml(course.title)}</small></td><td><span class="professor-gpa gpa-${gpaTone(course.avg_gpa)}">${course.avg_gpa == null ? "N/A" : Number(course.avg_gpa).toFixed(2)}</span></td><td>${Number(course.total_enrollment || 0).toLocaleString()}</td><td>${Number(course.sections_observed || 0).toLocaleString()}</td><td>${Number(course.terms_observed || 0).toLocaleString()}</td></tr>`).join("");
}

function coursePagerMarkup(page, total) {
  if (total <= COURSE_PAGE_SIZE) return "";
  const totalPages = Math.ceil(total / COURSE_PAGE_SIZE);
  const button = (label, targetPage, enabled, ariaLabel) => `<button class="section-page-button ${enabled ? "" : "is-placeholder"}" type="button" data-course-page-index="${targetPage}" aria-label="${ariaLabel}" title="${ariaLabel}" ${enabled ? "" : "disabled"}>${label}</button>`;
  return `<span class="section-page-label">Page ${page + 1} of ${totalPages}</span>${button("&#171;", 0, page > 0, "First course page")}${button("&#8249;", page - 1, page > 0, "Previous course page")}${button("&#8250;", page + 1, page < totalPages - 1, "Next course page")}${button("&#187;", totalPages - 1, page < totalPages - 1, "Last course page")}`;
}

function courseTableMarkup(courses) {
  const header = (label, key, active = key === "course_code") => `<th><button class="professor-sort course-sort" type="button" data-course-sort="${key}" aria-sort="${active ? "ascending" : "none"}">${label} <span>${active ? "↑" : "↕"}</span></button></th>`;
  const firstPageSize = Math.min(courses.length, COURSE_PAGE_SIZE);
  return `<div id="professor-course-pager-row" class="section-pager-row ${courses.length > COURSE_PAGE_SIZE ? "has-pager" : ""}"><p id="professor-course-count" class="section-count">Showing 1-${firstPageSize} of ${courses.length} courses</p><div id="professor-course-pager" class="show-more-sections">${coursePagerMarkup(0, courses.length)}</div></div><div class="table-scroll"><table class="detail-table"><thead><tr>${header("Course", "course_code")}${header("Average GPA", "avg_gpa")}${header("Enrollment", "total_enrollment")}${header("Sections", "sections_observed")}${header("Terms", "terms_observed")}</tr></thead><tbody id="professor-course-rows">${courseRowsMarkup(courses, "course_code", 1)}</tbody></table></div>`;
}

function renderProfessor(payload) {
  const { professor, current_sections: currentSections, course_summaries: courses, outcomes } = payload;
  const sections = currentSections.filter(isCollegeStationSection);
  const terms = [...new Set(sections.map((section) => String(section.term_code || "")).filter(Boolean))].sort().reverse();
  const currentTerm = String(payload.current_term_code || terms[0] || "");
  const selectedTerm = terms.includes(currentTerm) ? currentTerm : (terms[0] || "");
  const openSections = sections.filter((section) => String(section.seat_status_open || "").toUpperCase() === "Y").length;
  const gpa = professor.avg_gpa == null ? "--" : Number(professor.avg_gpa).toFixed(2);
  const tone = gpaTone(professor.avg_gpa);
  updatePageMetadata(
    `${professor.name} - Texas A&M Professor History | Aggie Courses`,
    `Explore ${professor.name}'s Texas A&M teaching history, courses, sections, enrollment, and grade-report trends.`,
  );
  professorPage.innerHTML = `<section class="course-hero course-overview professor-overview"><div class="course-overview-main"><h1 class="course-heading"><span class="course-code">Professor</span><span>${escapeHtml(professor.name)}</span></h1><p class="course-description">Teaching and grade-report history from ${professor.start_year} onward. GPA is enrollment-weighted across all reported sections.</p><div class="course-identity"><span>${Number(professor.courses_taught || 0)} course${Number(professor.courses_taught || 0) === 1 ? "" : "s"} taught</span><span>${Number(professor.sections_observed || 0).toLocaleString()} reported sections</span><span>${Number(professor.terms_observed || 0)} terms observed</span></div></div><aside class="course-summary metric-${tone}"><div class="course-quick-stats"><span class="section-availability-text ${openSections ? "open" : "unavailable"}"><i></i>${openSections ? `${openSections} section${openSections === 1 ? "" : "s"} open` : sections.length ? "No sections open" : "No College Station sections loaded"}</span></div><div class="summary-course-data"><div class="summary-report-navigation"><span>All available data</span></div><div class="summary-metric summary-gpa"><strong class="summary-gpa-value tone-${tone}">${gpa}</strong><span>Overall GPA</span></div><div class="summary-metric"><strong>${Number(professor.total_enrollment || 0).toLocaleString()}</strong><span>Enrolled</span></div><div class="summary-metric"><strong>${Number(professor.gpa_weight || 0).toLocaleString()}</strong><span>Graded</span></div></div></aside></section><section class="course-tabs professor-tabs" aria-label="Professor details"><div class="course-tab-list" role="tablist"><button type="button" role="tab" id="tab-professor-sections" aria-selected="true" aria-controls="panel-professor-sections" data-professor-tab="sections">Sections</button><button type="button" role="tab" id="tab-professor-performance" aria-selected="false" aria-controls="panel-professor-performance" data-professor-tab="performance">Performance</button><button type="button" role="tab" id="tab-professor-courses" aria-selected="false" aria-controls="panel-professor-courses" data-professor-tab="courses">Courses</button></div><section id="panel-professor-sections" class="course-detail-section sections-panel course-tab-panel" role="tabpanel" aria-labelledby="tab-professor-sections"><div class="detail-section-heading"><div><p class="eyebrow">Plan your schedule</p><h2>${selectedTerm ? escapeHtml(collegeStationOfferingLabel(selectedTerm)) : "Current College Station"} sections</h2></div><div class="section-controls"><label class="section-term-filter">Semester<select id="professor-section-term-filter">${terms.length > 1 ? '<option value="">All loaded terms</option>' : ""}${terms.map((term) => `<option value="${escapeHtml(term)}" ${term === selectedTerm ? "selected" : ""}>${escapeHtml(collegeStationOfferingLabel(term, term !== selectedTerm))}</option>`).join("")}</select></label><label class="open-sections-filter"><input id="professor-open-sections-filter" type="checkbox" /> Open only</label></div></div><div id="professor-section-pager-row" class="section-pager-row"><p id="professor-section-count" class="section-count"></p></div><div id="professor-sections-table"></div></section><section id="panel-professor-performance" class="course-detail-section course-tab-panel" role="tabpanel" aria-labelledby="tab-professor-performance" hidden><div class="detail-section-heading"><div><p class="eyebrow">Historical outcomes</p><h2>Course performance over time</h2></div><p>Click a course to focus its trend.</p></div><div id="professor-course-trend"></div></section><section id="panel-professor-courses" class="course-detail-section course-tab-panel" role="tabpanel" aria-labelledby="tab-professor-courses" hidden><div class="detail-section-heading"><div><p class="eyebrow">Course portfolio</p><h2>Courses and outcomes</h2></div><p>Sort any column.</p></div>${courses.length ? courseTableMarkup(courses) : '<p class="empty">No grade-report course history is available.</p>'}</section></section>`;

  const termFilter = document.querySelector("#professor-section-term-filter");
  const openFilter = document.querySelector("#professor-open-sections-filter");
  const sectionCount = document.querySelector("#professor-section-count");
  const sectionTable = document.querySelector("#professor-sections-table");
  const sectionHeading = document.querySelector("#panel-professor-sections .detail-section-heading h2");
  termFilter.querySelectorAll("option").forEach((option) => {
    if (option.value) {
      option.textContent = collegeStationOfferingLabel(option.value, option.value !== currentTerm);
    }
  });
  const updateSections = () => {
    let visible = termFilter.value ? sections.filter((section) => String(section.term_code) === termFilter.value) : sections;
    if (openFilter.checked) visible = visible.filter((section) => String(section.seat_status_open || "").toUpperCase() === "Y");
    sectionHeading.textContent = termFilter.value
      ? `${collegeStationOfferingLabel(termFilter.value, termFilter.value !== currentTerm)} sections`
      : "All loaded College Station sections";
    visible = [...visible].sort((left, right) => String(left.course_code || "").localeCompare(String(right.course_code || "")) || String(left.section || "").localeCompare(String(right.section || ""), undefined, { numeric: true }));
    sectionCount.textContent = `Showing ${visible.length} of ${visible.length} sections`;
    sectionTable.innerHTML = detailedSectionsMarkup(visible, currentTerm);
  };
  termFilter.addEventListener("change", updateSections);
  openFilter.addEventListener("change", updateSections);
  updateSections();

  const performancePanel = document.querySelector("#panel-professor-performance");
  if (performancePanel) {
    performancePanel.insertAdjacentHTML("beforeend", `<div class="outcome-history-panel"><h3>Term-by-term section details</h3><p>View each section's total enrollment and grade counts.</p>${termOutcomeHistoryMarkup(outcomes, courses)}</div>`);
    performancePanel.addEventListener("click", (event) => {
      const button = event.target.closest(".section-detail-toggle");
      if (!button) return;
      const detailRow = document.querySelector(`#${button.dataset.detailId}`);
      if (!detailRow) return;
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", String(!expanded));
      button.textContent = expanded ? "View sections" : "Hide sections";
      detailRow.hidden = expanded;
    });
  }

  const trendTarget = document.querySelector("#professor-course-trend");
  let selectedCourse = null;
  const renderTrend = () => {
    trendTarget.innerHTML = courseTrendMarkup(outcomes, courses, selectedCourse);
    trendTarget.querySelectorAll("title").forEach((title) => title.remove());
  };
  renderTrend();
  trendTarget.addEventListener("click", (event) => {
    const course = event.target.closest("[data-course-id], .trend-point");
    if (!course) return;
    const courseId = course.dataset.courseId;
    if (!courseId) return;
    selectedCourse = selectedCourse === courseId ? null : courseId;
    renderTrend();
  });
  trendTarget.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const course = event.target.closest("[data-course-id], .trend-point");
    if (!course) return;
    event.preventDefault();
    const courseId = course.dataset.courseId;
    if (!courseId) return;
    selectedCourse = selectedCourse === courseId ? null : courseId;
    renderTrend();
  });
  trendTarget.addEventListener("pointerover", (event) => {
    const point = event.target.closest(".trend-point");
    const tooltip = trendTarget.querySelector(".trend-tooltip");
    if (!point || !tooltip) return;
    tooltip.innerHTML = `<strong>${escapeHtml(point.dataset.course)}</strong><span>${escapeHtml(point.dataset.term)} · ${escapeHtml(point.dataset.sections)} section${point.dataset.sections === "1" ? "" : "s"} · ${Number(point.dataset.enrollment || 0).toLocaleString()} enrolled</span><b>GPA ${escapeHtml(point.dataset.gpa)}</b>`;
    const pointBounds = point.getBoundingClientRect();
    const chartBounds = trendTarget.getBoundingClientRect();
    tooltip.style.left = `${pointBounds.left - chartBounds.left + pointBounds.width / 2}px`;
    tooltip.style.top = `${pointBounds.top - chartBounds.top}px`;
    tooltip.classList.add("visible");
  });
  trendTarget.addEventListener("pointerout", (event) => { if (event.target.closest(".trend-point")) trendTarget.querySelector(".trend-tooltip")?.classList.remove("visible"); });

  let courseSortKey = "course_code";
  let courseSortDirection = 1;
  let coursePage = 0;
  const courseRows = document.querySelector("#professor-course-rows");
  const courseCount = document.querySelector("#professor-course-count");
  const coursePager = document.querySelector("#professor-course-pager");
  const renderCoursePage = () => {
    const pageCount = Math.ceil(courses.length / COURSE_PAGE_SIZE);
    if (coursePage >= pageCount) coursePage = 0;
    const start = coursePage * COURSE_PAGE_SIZE;
    const visibleCount = Math.min(COURSE_PAGE_SIZE, courses.length - start);
    courseRows.innerHTML = courseRowsMarkup(courses, courseSortKey, courseSortDirection, coursePage);
    courseCount.textContent = `Showing ${start + 1}-${start + visibleCount} of ${courses.length} courses`;
    coursePager.innerHTML = coursePagerMarkup(coursePage, courses.length);
  };
  document.querySelector("#panel-professor-courses")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-course-sort]");
    const pageButton = event.target.closest("button[data-course-page-index]");
    if (!button && !pageButton) return;
    if (pageButton) {
      coursePage = Number(pageButton.dataset.coursePageIndex);
      renderCoursePage();
      return;
    }
    const key = button.dataset.courseSort;
    courseSortDirection = courseSortKey === key ? courseSortDirection * -1 : 1;
    courseSortKey = key;
    coursePage = 0;
    renderCoursePage();
    document.querySelectorAll("[data-course-sort]").forEach((control) => {
      const active = control === button;
      control.setAttribute("aria-sort", active ? (courseSortDirection === 1 ? "ascending" : "descending") : "none");
      control.querySelector("span").textContent = active ? (courseSortDirection === 1 ? "↑" : "↓") : "↕";
    });
  });

  document.querySelector(".professor-tabs")?.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-professor-tab]");
    if (!tab) return;
    const target = tab.dataset.professorTab;
    document.querySelectorAll("[data-professor-tab]").forEach((button) => button.setAttribute("aria-selected", String(button === tab)));
    document.querySelectorAll("#panel-professor-sections, #panel-professor-performance, #panel-professor-courses").forEach((panel) => { panel.hidden = panel.id !== `panel-professor-${target}`; });
  });
}

async function loadProfessor() {
  const pathKey = window.location.pathname.split("/").filter(Boolean).pop() || "";
  let key = pathKey;
  try { key = decodeURIComponent(pathKey); } catch { /* Let the API reject malformed keys. */ }
  const from = coursePathFromLegacyValue(new URLSearchParams(window.location.search).get("from"));
  if (from) storeSessionPath(COURSE_RETURN_STORAGE_KEY, from);
  const returnToCourse = from || readSessionPath(COURSE_RETURN_STORAGE_KEY);
  if (returnToCourse) professorBackLink.href = returnToCourse;
  removeLegacyFromParameter();
  try {
    const response = await fetch(`/api/professors/${encodeURIComponent(key)}`);
    if (!response.ok) throw new Error(response.status === 404 ? "This professor could not be found." : `Unable to load professor details (${response.status}).`);
    renderProfessor(await response.json());
  } catch (error) {
    professorPage.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

loadProfessor();
