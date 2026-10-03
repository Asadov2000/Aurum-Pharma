import { useState } from "react";

import { showHelpSpotlight } from "./contextHelpSpotlight";

export type HelpLocation = {
  pathname: string;
  operationKind?: "payments" | "refunds";
  inventorySection?: "saleable" | "customer_returns";
  readOnly?: boolean;
};

type HelpStep = {
  title: string;
  detail: string;
  target: HTMLElement | null;
};

type HelpTopic = {
  section: string;
  step: HelpStep;
  instructions: string[];
  warning?: string;
};

function isVisible(element: HTMLElement): boolean {
  for (let current: HTMLElement | null = element; current; current = current.parentElement) {
    const style = window.getComputedStyle(current);
    if (
      current.hidden ||
      current.getAttribute("aria-hidden") === "true" ||
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.visibility === "collapse"
    )
      return false;
  }
  return true;
}

function availableButton(root: HTMLElement, label: string): HTMLButtonElement | null {
  return (
    Array.from(root.querySelectorAll("button")).find(
      (button) => !button.disabled && isVisible(button) && button.textContent?.trim() === label,
    ) ?? null
  );
}

function availableField(root: HTMLElement | null, selector: string): HTMLElement | null {
  return Array.from(root?.querySelectorAll<HTMLElement>(selector) ?? []).find(isVisible) ?? null;
}

function helpFor({ pathname, operationKind, inventorySection, readOnly }: HelpLocation): HelpTopic {
  const root = document.querySelector<HTMLElement>("#main-content");
  if (readOnly && (pathname === "/suppliers" || pathname === "/batches")) {
    const suppliers = pathname === "/suppliers";
    return {
      section: suppliers ? "Поставщики · Локальные данные" : "Партии · Локальные данные",
      step: {
        title: suppliers
          ? "Найдите поставщика в локальных данных"
          : "Проверьте остатки в локальных данных",
        detail: suppliers
          ? "Ищите по названию, контакту или ИНН. Здесь доступны сведения из локального снимка."
          : "Проверьте остаток, срок годности и блокировку партии. Здесь доступны сведения из локального снимка.",
        target: availableField(
          root,
          suppliers
            ? "#edge_supplier_search:not(:disabled)"
            : 'input[type="checkbox"]:not(:disabled)',
        ),
      },
      instructions: suppliers
        ? [
            "Найдите компанию через поиск.",
            "Посмотрите контакты и реквизиты в её карточке.",
            "В этом режиме доступен только просмотр; добавление и изменение поставщиков недоступны.",
          ]
        : [
            "Просмотрите остатки и сроки годности партий.",
            "При необходимости включите «Показывать партии без остатка».",
            "В этом режиме доступен только просмотр; складские операции недоступны.",
          ],
      warning: "Сведения относятся к локальному снимку и могут отличаться от данных сервера.",
    };
  }
  if (pathname === "/suppliers") {
    return {
      section: "Поставщики",
      step: {
        title: "Найдите поставщика",
        detail: "Ищите по названию, контакту, телефону или ИНН, затем откройте карточку компании.",
        target: availableField(root, "#supplier_search:not(:disabled)"),
      },
      instructions: [
        "Найдите компанию через поиск. Чтобы увидеть отключённых поставщиков, измените фильтр «Статус».",
        "Откройте карточку и проверьте контакты и реквизиты перед работой с поставкой.",
        "Если компании нет и доступна кнопка «Добавить поставщика», создайте карточку с её реквизитами.",
      ],
      warning:
        "Перед добавлением проверьте существующие карточки, включая отключённые. Не создавайте второго поставщика для той же компании.",
    };
  }
  if (pathname === "/batches") {
    const returns = inventorySection === "customer_returns";
    return {
      section: returns ? "Возвраты покупателей" : "Партии",
      step: {
        title: returns ? "Найдите возвращённый товар" : "Найдите партию",
        detail: returns
          ? "Найдите товар, партию или номер чека. По умолчанию показаны возвраты, которые ожидают решения."
          : "Укажите номер партии или выберите товар, затем проверьте остаток и срок годности.",
        target: availableField(
          root,
          returns
            ? "#customer_return_search:not(:disabled)"
            : "#batch_number_filter:not(:disabled), #batch_catalog_filter:not(:disabled)",
        ),
      },
      instructions: returns
        ? [
            "Найдите возврат по товару, партии или номеру чека.",
            "Чтобы увидеть завершённые возвраты, измените фильтр «Статус».",
            "Если доступна кнопка «Принять решение», укажите выполненное действие и причину. Проверьте данные перед сохранением.",
          ]
        : [
            "Найдите партию по номеру или товару.",
            "Уточните точку, срок годности и доступность с помощью фильтров.",
            "Откройте карточку партии и проверьте остаток и движения. Новые партии появляются после принятия поставки.",
          ],
      warning: returns
        ? "Возвращённый товар не становится доступным для продажи. Сохранённое решение нельзя изменить."
        : "Остаток меняется через складские операции. Перед продажей проверьте срок годности и отсутствие блокировки партии.",
    };
  }
  if (pathname === "/payment-reconciliation") {
    const refunds = operationKind === "refunds";
    return {
      section: refunds ? "Сверка возвратов" : "Сверка оплат",
      step: {
        title: refunds ? "Проверьте результат возврата" : "Проверьте результат оплаты",
        detail:
          "Обновите список, затем сверьте сумму и номер операции с документом банка или терминала.",
        target: root && availableButton(root, "Обновить"),
      },
      instructions: refunds
        ? [
            "Обновите список и откройте чек нужного возврата. В чеке продолжите существующую заявку через «Оформить возврат».",
            "Сверьте результат с документом банка или терминала; укажите подтверждение в чеке.",
            "Если деньги возвращены, завершите оформление возвратного чека. Статус «Ждут чека» означает, что оформление ещё не закончено.",
          ]
        : [
            "Обновите список и выберите оплату, результат которой нужно проверить.",
            "Сверьте документ банка или терминала. Выберите «Оплата прошла» либо «Оплаты нет», укажите реквизиты подтверждения и сохраните решение.",
            "Подтверждённая оплата ещё может ждать завершения чека. Откройте чек и проверьте, завершена ли продажа.",
          ],
      warning: refunds
        ? "Если результат неизвестен, не повторяйте возврат денег. Сначала получите подтверждение банка или терминала."
        : "Если результат неизвестен, не принимайте оплату повторно. Сначала получите подтверждение банка или терминала.",
    };
  }
  if (pathname === "/pos") {
    const complete = root && availableButton(root, "Завершить продажу");
    const searchCandidate = root?.querySelector<HTMLInputElement>(
      'input[placeholder="Найти товар или отсканировать штрих-код"]:not(:disabled)',
    );
    const search = searchCandidate && isVisible(searchCandidate) ? searchCandidate : null;
    return {
      section: "Касса",
      step: complete
        ? {
            title: "Проверьте оплату и завершите продажу",
            detail: "После подтверждения оплаты убедитесь, что появится номер оформленного чека.",
            target: complete,
          }
        : search
          ? {
              title: "Найдите товар",
              detail:
                "Введите название или отсканируйте штрихкод, затем проверьте товар перед добавлением.",
              target: search,
            }
          : {
              title: "Подготовьте кассу",
              detail: "Выберите доступную рабочую кассу и откройте смену, если это требуется.",
              target: null,
            },
      instructions: [
        "Выберите рабочую кассу и проверьте, открыта ли смена.",
        "Добавьте товары и примите оплату выбранным способом.",
        "Завершите продажу и убедитесь, что появился номер чека.",
      ],
      warning:
        "Если результат оплаты неизвестен, сначала проверьте его в сверке. Не повторяйте оплату наугад.",
    };
  }
  if (pathname.startsWith("/incoming/")) {
    const continueItem = root && availableButton(root, "Продолжить");
    const addItem = root && availableButton(root, "Добавить позицию");
    const accept = root && availableButton(root, "Принять на склад");
    const target = continueItem ?? addItem ?? accept;
    return {
      section: "Приёмка товаров",
      step: continueItem
        ? {
            title: "Завершите незавершённую позицию",
            detail: "Проверьте товар, количество и срок годности до принятия поставки.",
            target: continueItem,
          }
        : addItem
          ? {
              title: "Проверьте позиции поставки",
              detail: "Добавьте недостающие товары и сверьте их с накладной.",
              target: addItem,
            }
          : {
              title: accept ? "Проверьте поставку перед принятием" : "Просмотрите документ",
              detail: accept
                ? "После принятия документ нельзя изменить. Сверьте товары и суммы с накладной."
                : "Если документ уже завершён, он доступен для просмотра.",
              target,
            },
      instructions: [
        "Сверьте поставщика и номер накладной.",
        "Проверьте каждую позицию, количество и срок годности.",
        "Только после проверки примите товары на склад.",
      ],
      warning: "Принятый документ нельзя изменить. Ошибку исправляют отдельной операцией.",
    };
  }
  if (pathname === "/incoming") {
    return {
      section: "Приёмка товаров",
      step: {
        title: "Откройте или создайте поставку",
        detail: "Работайте по накладной поставщика. Данные можно проверить до принятия.",
        target: root && availableButton(root, "Новая приёмка"),
      },
      instructions: [
        "Создайте документ или откройте существующий.",
        "Сверьте реквизиты, позиции и сроки годности.",
        "Примите товары после полной проверки.",
      ],
      warning: "Принятый документ нельзя изменить. Ошибку исправляют отдельной операцией.",
    };
  }
  return {
    section: "Выплаты сотрудникам",
    step: {
      title: "Найдите сотрудника",
      detail: "Откройте его период выплат, затем запишите только уже выданные наличные.",
      target: (() => {
        const input = root?.querySelector<HTMLInputElement>("#pay-employee:not(:disabled)");
        return input && isVisible(input) ? input : null;
      })(),
    },
    instructions: [
      "Найдите сотрудника и откройте нужный период.",
      "Укажите согласованную сумму вручную, если период ещё не создан.",
      "После фактической выдачи наличных запишите выплату или аванс и проверьте историю.",
    ],
    warning:
      "Запись выплаты не выдаёт деньги и не списывает наличные из кассы. Аванс учитывается в выданной сумме один раз.",
  };
}

export function ContextHelp({
  pathname,
  operationKind,
  inventorySection,
  readOnly,
  close,
}: HelpLocation & {
  close: () => void;
}): JSX.Element {
  const [showInstructions, setShowInstructions] = useState(false);
  const topic = helpFor({ pathname, operationKind, inventorySection, readOnly });
  return (
    <div className="space-y-4 p-4 text-sm text-foreground">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-wide text-primary">
          Подсказка · {topic.section}
        </p>
        <h3 className="mt-2 text-base font-semibold">{topic.step.title}</h3>
        <p className="mt-1 leading-6 text-foreground-secondary">{topic.step.detail}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {topic.step.target ? (
          <button
            type="button"
            className="min-h-10 rounded-md bg-primary/10 px-3 font-medium text-primary hover:bg-primary/15"
            onClick={() => {
              const target = helpFor({ pathname, operationKind, inventorySection, readOnly }).step
                .target;
              if (!target) return;
              close();
              showHelpSpotlight(target);
            }}
          >
            Показать на экране
          </button>
        ) : null}
        <button
          type="button"
          className="min-h-10 rounded-md px-3 font-medium text-foreground-secondary hover:bg-foreground/5"
          aria-expanded={showInstructions}
          onClick={() => setShowInstructions((current) => !current)}
        >
          {showInstructions ? "Скрыть шаги" : "Инструкция по шагам"}
        </button>
      </div>
      {showInstructions ? (
        <ol className="list-decimal space-y-2 border-t border-border pt-3 pl-5 leading-5 text-foreground-secondary">
          {topic.instructions.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      ) : null}
      {topic.warning ? (
        <p className="rounded-md border border-warning/30 bg-warning-subtle px-3 py-2 text-xs leading-5 text-warning-foreground">
          {topic.warning}
        </p>
      ) : null}
    </div>
  );
}
