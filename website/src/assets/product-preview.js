// Illustrative local examples only. This page never requests pharmacy data.
const examples = {
  owner: {
    title: "Что происходит в аптеке",
    metrics: [
      ["Продажи за день", "1 320", "TJS"],
      ["Чеков", "48"],
      ["Аптечные точки", "3"],
    ],
    heading: "Продажи в течение дня",
    note: "Видеть продажи, смены и остатки по своим точкам. Демонстрационные данные.",
    navigation: "Обзор",
    rows: [
      ["Центральная аптека", "Смена открыта", true],
      ["Аптека на Сомони", "Смена открыта", true],
      ["Аптека на Рудаки", "Смена закрыта"],
    ],
  },
  cashier: {
    title: "Покупатель перед вами. Всё нужное рядом.",
    metrics: [
      ["Текущий чек", "20", "TJS"],
      ["Позиций", "3"],
      ["Смена", "Открыта"],
    ],
    heading: "Поиск → Чек → Оплата",
    note: "Находить товары и собирать чек в одном рабочем пространстве. Демонстрационные данные.",
    navigation: "Касса",
    rows: [
      ["Бинт медицинский · 1 шт.", "8,00 TJS"],
      ["Пластырь · 2 шт.", "10,00 TJS"],
      ["Маска медицинская · 1 шт.", "2,00 TJS"],
    ],
  },
  stock: {
    title: "Поставка — в понятном документе",
    metrics: [
      ["Приход", "№ 024"],
      ["Позиций", "12"],
      ["Статус", "Черновик"],
    ],
    heading: "Товар → Партия → Приёмка",
    note: "Собирать приход постепенно и дополнять каталог по ходу работы. Демонстрационные данные.",
    navigation: "Приходы",
    rows: [
      ["Бинт медицинский", "Товар выбран", true],
      ["Пластырь", "Товар выбран", true],
      ["Маска медицинская", "Укажите партию"],
    ],
  },
};

function showExample(key) {
  if (!Object.hasOwn(examples, key)) return;
  const example = examples[key];
  document.querySelector(".chart-row").dataset.view =
    key === "owner" ? "chart" : "workflow";
  document.querySelectorAll("[data-role]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.role === key));
  });
  document.querySelectorAll(".app-nav span").forEach((item) => {
    item.classList.toggle("selected", item.textContent === example.navigation);
  });
  document.getElementById("preview-title").textContent = example.title;
  example.metrics.forEach(([label, value, unit], index) => {
    const suffix = ["a", "b", "c"][index];
    document.getElementById(`metric-${suffix}`).textContent = label;
    const target = document.getElementById(`value-${suffix}`);
    target.textContent = value;
    if (unit) {
      const currency = document.createElement("em");
      currency.textContent = unit;
      target.append(" ", currency);
    }
  });
  document.getElementById("chart-title").textContent = example.heading;
  document.getElementById("preview-note").textContent = example.note;
  document.querySelector(".mini-list").replaceChildren(
    ...example.rows.map(([label, value, badge]) => {
      const row = document.createElement("div");
      const name = document.createElement("span");
      name.textContent = label;
      const status = document.createElement("span");
      status.textContent = value;
      if (badge) status.className = "badge";
      row.append(name, status);
      return row;
    }),
  );
}

document.querySelectorAll("[data-role]").forEach((button) => {
  button.addEventListener("click", () => showExample(button.dataset.role));
});
document.querySelectorAll("[data-preview]").forEach((link) => {
  link.addEventListener("click", () => showExample(link.dataset.preview));
});
document.querySelector(".role-pills").hidden = false;
