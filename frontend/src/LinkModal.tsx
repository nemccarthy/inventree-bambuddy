import type { InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Button,
  Divider,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text
} from '@mantine/core';
import { useEffect, useMemo, useRef, useState } from 'react';

import { apiData, apiError, fieldErrors, fmtDuration, fmtGrams, fmtQty } from './format';
import type {
  ConfigData,
  FileDetail,
  LibraryFile,
  Link,
  PartLabel,
  Settings
} from './types';
import { Filaments, LocationSelect } from './ui';

const SLICED_MODEL = 'model';

function numberOrNull(value: number | string): number | null {
  if (value === '' || value === null || value === undefined) {
    return null;
  }

  const number = Number(value);

  return Number.isFinite(number) ? number : null;
}

/** Dialog: link a part to a sliced file in Bambuddy, or change the link */
export function LinkModal({
  context,
  slug,
  part,
  link,
  settings,
  opened,
  onClose,
  onSaved
}: Readonly<{
  context: InvenTreePluginContext;
  slug: string;
  part: PartLabel;
  link: Link | null;
  settings: Settings;
  opened: boolean;
  onClose: () => void;
  onSaved: () => void;
}>) {
  const api = useRef(context.api);
  api.current = context.api;

  // The form starts from the link as it is when the dialog opens. A refresh of
  // the page behind it must not reset what is being typed.
  const initial = useRef(link);

  if (!opened) {
    initial.current = link;
  }

  const [files, setFiles] = useState<LibraryFile[] | null>(null);
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [detail, setDetail] = useState<FileDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [fileId, setFileId] = useState<number | null>(null);
  const [plateId, setPlateId] = useState<number | null>(null);
  const [perPlate, setPerPlate] = useState<number | string>('');
  const [target, setTarget] = useState<string>(SLICED_MODEL);
  const [targetLocation, setTargetLocation] = useState<string | null>(null);
  const [auto, setAuto] = useState(false);
  const [restockTo, setRestockTo] = useState<number | string>('');
  const [maxPlates, setMaxPlates] = useState<number | string>('');
  const [destination, setDestination] = useState<number | null>(null);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirmRemove, setConfirmRemove] = useState(false);

  // Load the library and the printers, and start from the stored link
  useEffect(() => {
    if (!opened) {
      return;
    }

    let cancelled = false;

    setFiles(null);
    setLoadError(null);
    setError(null);
    setErrors({});
    setConfirmRemove(false);

    const start = initial.current;

    setFileId(start?.file_id ?? null);
    setPlateId(start?.plate_id ?? null);
    setPerPlate(start?.parts_per_plate ?? '');
    setTarget(start?.printer_id ? `printer:${start.printer_id}` : SLICED_MODEL);
    setTargetLocation(start?.target_location ?? null);
    setAuto(start?.auto ?? false);
    setRestockTo(start?.restock_to ?? '');
    setMaxPlates(start?.max_plates ?? '');
    setDestination(start?.destination ?? null);

    Promise.all([
      api.current.get(`/plugin/${slug}/files/`),
      api.current.get(`/plugin/${slug}/config/`)
    ])
      .then(([fileResponse, configResponse]) => {
        if (cancelled) {
          return;
        }

        setFiles(apiData<{ files: LibraryFile[] }>(fileResponse, 'files').files);
        setConfig(apiData<ConfigData>(configResponse, 'bambuddy'));
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(apiError(err, 'Could not read the Bambuddy library'));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [opened, slug]);

  // The plates of the chosen file
  useEffect(() => {
    if (!opened || fileId === null) {
      setDetail(null);
      return;
    }

    let cancelled = false;

    setDetail(null);

    api.current
      .get(`/plugin/${slug}/files/${fileId}/`)
      .then((response) => {
        if (!cancelled) {
          setDetail(apiData<FileDetail>(response, 'plates'));
        }
      })
      .catch(() => {
        // The link can still be saved: the server checks the file again
      });

    return () => {
      cancelled = true;
    };
  }, [opened, fileId, slug]);

  const fileOptions = useMemo(() => {
    const options = (files ?? []).map((file) => ({
      value: String(file.id),
      label: file.folder ? `${file.folder} / ${file.filename}` : file.filename
    }));

    // A linked file which has since been removed from Bambuddy
    if (link && files && !files.some((file) => file.id === link.file_id)) {
      options.unshift({
        value: String(link.file_id),
        label: `${link.filename ?? 'File'} (no longer in Bambuddy)`
      });
    }

    return options;
  }, [files, link]);

  const chosen = files?.find((file) => file.id === fileId) ?? null;
  const slicedFor = detail?.sliced_for_model ?? chosen?.sliced_for_model ?? null;
  const multiPlate = (detail?.plates.length ?? 0) > 1;
  const plate =
    detail?.plates.find((entry) => entry.index === plateId) ?? detail?.plates[0] ?? null;
  const others = (chosen?.linked_to ?? []).filter((other) => other.pk !== part.pk);

  const printers = config?.bambuddy.printers ?? [];

  const targetOptions = useMemo(
    () => [
      {
        value: SLICED_MODEL,
        label: slicedFor ? `Any ${slicedFor}` : 'Any printer of the model the file was sliced for'
      },
      ...printers
        .filter((printer) => printer.active)
        .map((printer) => ({
          value: `printer:${printer.id}`,
          label: [printer.name, printer.model, printer.location].filter(Boolean).join(' · ')
        }))
    ],
    [printers, slicedFor]
  );

  const locationOptions = useMemo(
    () =>
      Array.from(
        new Set(
          printers
            .filter((printer) => printer.location && (!slicedFor || printer.model === slicedFor))
            .map((printer) => String(printer.location))
        )
      ).sort(),
    [printers, slicedFor]
  );

  const save = () => {
    setBusy('save');
    setError(null);
    setErrors({});

    const printerId = target.startsWith('printer:') ? Number(target.slice(8)) : null;

    api.current
      .put(`/plugin/${slug}/part/${part.pk}/`, {
        file_id: fileId,
        plate_id: multiPlate ? plateId : null,
        parts_per_plate: numberOrNull(perPlate),
        printer_id: printerId,
        target_model: null,
        target_location: printerId ? null : targetLocation,
        auto,
        restock_to: numberOrNull(restockTo),
        max_plates: numberOrNull(maxPlates),
        destination
      })
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((err: any) => {
        const fields = fieldErrors(err);

        setErrors(fields);

        if (Object.keys(fields).length === 0 || fields.detail) {
          setError(apiError(err, 'Could not save the link'));
        }
      })
      .finally(() => setBusy(null));
  };

  const remove = () => {
    setBusy('remove');
    setError(null);

    api.current
      .delete(`/plugin/${slug}/part/${part.pk}/`)
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((err: any) => setError(apiError(err, 'Could not remove the link')))
      .finally(() => setBusy(null));
  };

  const autoActive = settings.auto_low_stock || settings.auto_build_demand;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={link ? `3D print link of ${part.name}` : `Link ${part.name} to a sliced file`}
      size='lg'
      centered
    >
      {loadError ? (
        <Alert color='red' title='Bambuddy could not be reached'>
          {loadError}
        </Alert>
      ) : files === null ? (
        <Group justify='center' p='xl'>
          <Loader />
        </Group>
      ) : (
        <Stack gap='md'>
          <Select
            label='Sliced file in Bambuddy'
            description='Files of type .gcode.3mf or .gcode from the Bambuddy file manager. One part type on the plate.'
            placeholder={files.length === 0 ? 'No sliced files in Bambuddy yet' : 'Choose a file'}
            data={fileOptions}
            value={fileId === null ? null : String(fileId)}
            onChange={(value) => {
              setFileId(value ? Number(value) : null);
              setPlateId(null);
            }}
            searchable
            nothingFoundMessage='No file found'
            error={errors.file_id}
            comboboxProps={{ withinPortal: true }}
            data-autofocus
          />

          {chosen && (
            <Stack gap={4}>
              <Group gap='md' wrap='wrap'>
                <Text size='sm'>
                  {[
                    slicedFor ? `Sliced for ${slicedFor}` : 'Printer model not recorded',
                    fmtDuration(plate?.print_time_seconds ?? chosen.print_time_seconds),
                    fmtGrams(plate?.filament_grams ?? chosen.filament_grams)
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </Text>
                <Filaments filaments={plate?.filaments} />
              </Group>
              {others.length > 0 && (
                <Text size='sm' c='orange'>
                  Also linked to {others.map((other) => other.name).join(', ')}. A plate is booked
                  as one part type.
                </Text>
              )}
            </Stack>
          )}

          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <NumberInput
              label='Parts per plate'
              description='How many of this part one plate prints'
              value={perPlate}
              onChange={setPerPlate}
              min={1}
              allowDecimal={false}
              error={errors.parts_per_plate}
              withAsterisk
            />
            {multiPlate ? (
              <Select
                label='Plate'
                description='This file has more than one plate'
                placeholder='Choose a plate'
                data={(detail?.plates ?? []).map((entry) => ({
                  value: String(entry.index),
                  label: `Plate ${entry.index}${entry.name ? `: ${entry.name}` : ''}`
                }))}
                value={plateId === null ? null : String(plateId)}
                onChange={(value) => setPlateId(value ? Number(value) : null)}
                error={errors.plate_id}
                comboboxProps={{ withinPortal: true }}
                withAsterisk
              />
            ) : (
              <LocationSelect
                context={context}
                label='Stock location'
                description='Where the printed parts go'
                placeholder='Default location of the part'
                value={destination}
                valueLabel={link?.destination_name}
                onChange={setDestination}
                error={errors.destination}
              />
            )}
          </SimpleGrid>

          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select
              label='Print on'
              data={targetOptions}
              value={target}
              onChange={(value) => setTarget(value ?? SLICED_MODEL)}
              allowDeselect={false}
              error={errors.printer_id}
              comboboxProps={{ withinPortal: true }}
            />
            {target === SLICED_MODEL ? (
              <Select
                label='Only at'
                placeholder='Any location'
                data={locationOptions}
                value={targetLocation}
                onChange={setTargetLocation}
                clearable
                disabled={locationOptions.length === 0}
                comboboxProps={{ withinPortal: true }}
              />
            ) : (
              <div />
            )}
          </SimpleGrid>

          {multiPlate && (
            <LocationSelect
              context={context}
              label='Stock location'
              description='Where the printed parts go'
              placeholder='Default location of the part'
              value={destination}
              valueLabel={link?.destination_name}
              onChange={setDestination}
              error={errors.destination}
            />
          )}

          <Divider />

          <Switch
            label='Print automatically'
            description={
              autoActive
                ? 'Queue a print run for this part when the automatic check finds it short. Runs are staged: somebody still releases them.'
                : 'Automatic printing is switched off in the plugin settings, so this has no effect yet.'
            }
            checked={auto}
            onChange={(event) => setAuto(event.currentTarget.checked)}
          />

          {auto && (
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <NumberInput
                label='Print up to'
                description={`When stock falls below the minimum (${fmtQty(part.minimum_stock)}). Empty: up to the minimum.`}
                value={restockTo}
                onChange={setRestockTo}
                min={0}
                allowDecimal={false}
                error={errors.restock_to}
              />
              <NumberInput
                label='Most plates at once'
                description={`Empty: ${fmtQty(settings.max_plates)}, from the plugin settings`}
                value={maxPlates}
                onChange={setMaxPlates}
                min={1}
                allowDecimal={false}
                error={errors.max_plates}
              />
            </SimpleGrid>
          )}

          {!part.assembly && !link && (
            <Text size='sm' c='dimmed'>
              Saving marks this part as an assembly. InvenTree only raises build orders for
              assemblies, and every print run is a build order.
            </Text>
          )}

          {error && <Alert color='red'>{error}</Alert>}

          <Group justify='space-between'>
            {link ? (
              confirmRemove ? (
                <Group gap='xs'>
                  <Button color='red' size='xs' loading={busy === 'remove'} onClick={remove}>
                    Remove the link
                  </Button>
                  <Button variant='default' size='xs' onClick={() => setConfirmRemove(false)}>
                    Keep it
                  </Button>
                </Group>
              ) : (
                <Button variant='subtle' color='red' onClick={() => setConfirmRemove(true)}>
                  Remove link
                </Button>
              )
            ) : (
              <div />
            )}
            <Group gap='xs'>
              <Button variant='default' onClick={onClose}>
                Cancel
              </Button>
              <Button onClick={save} loading={busy === 'save'} disabled={fileId === null}>
                Save
              </Button>
            </Group>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
