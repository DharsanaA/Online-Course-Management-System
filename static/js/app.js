/* Client-side behaviour: confirmations, quiz timer and auto-submit, dynamic quiz builder, chart helper. */
(function () {
  "use strict";
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm") || (event.submitter && event.submitter.getAttribute("data-confirm"));
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });
  var timer = document.getElementById("quiz-timer");
  var quizForm = document.getElementById("quiz-form");
  if (timer && quizForm) {
    var remaining = parseInt(timer.getAttribute("data-seconds"), 10) || 0;
    var render = function () {
      var m = Math.floor(remaining / 60), s = remaining % 60;
      timer.textContent = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
      timer.classList.toggle("text-danger", remaining <= 60);
    };
    render();
    var tick = setInterval(function () {
      remaining -= 1;
      render();
      if (remaining <= 0) {
        clearInterval(tick);
        quizForm.submit();
      }
    }, 1000);

    window.addEventListener("beforeunload", function (e) {
      if (!quizForm.dataset.submitting) { e.preventDefault(); e.returnValue = ""; }
    });
    quizForm.addEventListener("submit", function () { quizForm.dataset.submitting = "1"; });
  }

  var container = document.getElementById("questions");
  var template = document.getElementById("question-template");
  var addBtn = document.getElementById("add-question");
  if (container && template && addBtn) {
    var nextIndex = container.querySelectorAll(".question-card").length;
    var renumber = function () {
      container.querySelectorAll(".question-card").forEach(function (card, n) {
        card.querySelector(".q-number").textContent = n + 1;
      });
    };
    addBtn.addEventListener("click", function () {
      var html = template.innerHTML.replace(/__i__/g, nextIndex);
      nextIndex += 1;
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html.trim();
      container.appendChild(wrapper.firstElementChild);
      renumber();
    });
    container.addEventListener("click", function (event) {
      if (event.target.closest(".remove-question")) {
        event.target.closest(".question-card").remove();
        renumber();
      }
    });
    renumber();
  }

  // 4. Chart helper used by pages: data-chart="bar|line|doughnut|pie" on a <canvas> with data-labels/data-values
  document.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
    if (!window.Chart) return;
    var labels = JSON.parse(canvas.getAttribute("data-labels") || "[]");
    var values = JSON.parse(canvas.getAttribute("data-values") || "[]");
    var label = canvas.getAttribute("data-label") || "Value";
    if (!labels.length) {
      canvas.parentElement.insertAdjacentHTML("beforeend",
        '<p class="text-secondary small text-center mb-0 py-4">No data yet.</p>');
      canvas.remove();
      return;
    }
    var palette = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4f46e5"];
    new Chart(canvas, {
      type: canvas.getAttribute("data-chart"),
      data: {
        labels: labels,
        datasets: [{
          label: label, data: values,
          backgroundColor: canvas.getAttribute("data-chart") === "line" ? "rgba(37,99,235,.15)" : palette,
          borderColor: "#2563eb", borderWidth: 2, fill: canvas.getAttribute("data-chart") === "line", tension: .35
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: ["doughnut", "pie"].indexOf(canvas.getAttribute("data-chart")) >= 0 } },
        scales: ["doughnut", "pie"].indexOf(canvas.getAttribute("data-chart")) >= 0 ? {} : { y: { beginAtZero: true, ticks: { precision: 0 } } }
      }
    });
  });
})();
