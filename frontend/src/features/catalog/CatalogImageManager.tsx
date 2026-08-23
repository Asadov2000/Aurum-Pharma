import { useRef, useState } from "react";

import { Button, ConfirmDialog } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { CatalogImage } from "./CatalogImage";
import { useDeleteCatalogImage, useUploadCatalogImage } from "./mediaQueries";
import { useCatalogItemQuery } from "./queries";
import { type CatalogItem } from "./types";

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set(["image/jpeg", "image/png"]);

export function CatalogImageManager({
  item,
  canManage,
}: {
  item: CatalogItem;
  canManage: boolean;
}): JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const uploadMutation = useUploadCatalogImage();
  const deleteMutation = useDeleteCatalogImage();
  const detail = useCatalogItemQuery(item.id);
  const current = detail.data ?? item;
  const isBusy = uploadMutation.isPending || deleteMutation.isPending;

  const upload = async (file: File) => {
    setError(null);
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      setError("Выберите изображение JPG или PNG.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError("Изображение больше 5 МБ. Выберите файл меньшего размера.");
      return;
    }
    try {
      await uploadMutation.mutateAsync({ itemId: current.id, file });
    } catch (uploadError) {
      setError(describeApiError(uploadError, "Не удалось загрузить фотографию"));
    }
  };

  const remove = async () => {
    setError(null);
    try {
      await deleteMutation.mutateAsync(current.id);
      setConfirmDelete(false);
    } catch (deleteError) {
      setError(describeApiError(deleteError, "Не удалось удалить фотографию"));
    }
  };

  return (
    <section className="space-y-3" aria-label="Фотография">
      <CatalogImage item={current} variant="detail" />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-foreground">
            {current.image_version ? "Фотография товара" : "Фото не добавлено"}
          </p>
          <p className="mt-0.5 text-xs text-foreground-muted">
            JPG или PNG · до 5 МБ · фотография необязательна
          </p>
        </div>
        {canManage && !current.deleted_at && (
          <div className="flex flex-wrap gap-2">
            <input
              ref={inputRef}
              type="file"
              accept="image/jpeg,image/png"
              aria-label="Выбрать фотографию товара"
              className="sr-only"
              disabled={isBusy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
                event.target.value = "";
              }}
            />
            <Button
              variant="secondary"
              size="sm"
              isLoading={uploadMutation.isPending}
              disabled={isBusy}
              onClick={() => inputRef.current?.click()}
            >
              {current.image_version ? "Заменить" : "+ Добавить фото"}
            </Button>
            {current.image_version && (
              <Button
                variant="ghost"
                size="sm"
                disabled={isBusy}
                onClick={() => setConfirmDelete(true)}
              >
                Удалить
              </Button>
            )}
          </div>
        )}
      </div>
      {error && (
        <p className="mt-2 text-sm text-danger" role="alert">
          {error}
        </p>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Удалить фотографию"
        message="Удалить фотографию товара? Сама позиция каталога останется без изменений."
        confirmLabel="Удалить фото"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmDelete(false)}
      />
    </section>
  );
}
