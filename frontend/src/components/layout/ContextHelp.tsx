import { useState } from "react";

import { showHelpSpotlight } from "./contextHelpSpotlight";

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

function helpFor(pathname: string): HelpTopic {
  const root = document.querySelector<HTMLElement>("#main-content");
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
  close,
}: {
  pathname: string;
  close: () => void;
}): JSX.Element {
  const [showInstructions, setShowInstructions] = useState(false);
  const topic = helpFor(pathname);
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
              const target = helpFor(pathname).step.target;
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
