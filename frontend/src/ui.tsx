import type { InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  ColorSwatch,
  Group,
  Modal,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  Tooltip
} from '@mantine/core';
import {
  IconCheck,
  IconExternalLink,
  IconPlayerPlay,
  IconPrinter,
  IconX
} from '@tabler/icons-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

import {
  ITEM_STATE,
  RUN_STATE,
  SOURCE_LABEL,
  apiError,
  fmtDateTime,
  fmtDuration,
  fmtGrams,
  fmtQty,
  platesFor,
  plural,
  targetLabel
} from './format';
import type {
  Filament,
  Link,
  PartLabel,
  Permissions,
  QueueItem,
  Run,
  Settings
} from './types';

/** The rows of an InvenTree list response, paginated or not */
export function listRows(response: any): any[] {
  const data = response?.data;
  const rows = Array.isArray(data) ? data : data?.results;

  return Array.isArray(rows) ? rows : [];
}

export function partUrl(pk: number): string {
  return `/part/${pk}/`;
}

export function buildUrl(pk: number): string {
  return `/manufacturing/build-order/${pk}/`;
}

/** A link which navigates inside the InvenTree single page app */
export function AppLink({
  context,
  to,
  children,
  title
}: Readonly<{
  context: InvenTreePluginContext;
  to: string;
  children: ReactNode;
  title?: string;
}>) {
  return (
    <Anchor
      href={`/web${to}`}
      title={title}
      size='sm'
      onClick={(event) => {
        // Let the browser handle ctrl / cmd / middle click (open in a new tab)
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) {
          return;
        }

        event.preventDefault();
        context.navigate(to);
      }}
    >
      {children}
    </Anchor>
  );
}

/** A number with a label, for the row of figures at the top of a panel */
export function Figure({
  label,
  value,
  hint,
  color
}: Readonly<{ label: string; value: string; hint?: ReactNode; color?: string }>) {
  return (
    <Paper withBorder p='sm'>
      <Text size='xs' c='dimmed' tt='uppercase' fw={600}>
        {label}
      </Text>
      <Text fz={24} fw={700} lh={1.25} c={color} style={{ fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </Text>
      {hint && (
        <Text size='xs' c='dimmed'>
          {hint}
        </Text>
      )}
    </Paper>
  );
}

/** Filament of a file: a colour swatch and the material for each one used */
export function Filaments({ filaments }: Readonly<{ filaments: Filament[] | undefined }>) {
  if (!filaments || filaments.length === 0) {
    return null;
  }

  return (
    <Group gap={8} wrap='wrap'>
      {filaments.map((filament, index) => (
        <Group gap={4} wrap='nowrap' key={`${filament.type}-${index}`}>
          {filament.color && (
            <ColorSwatch color={filament.color} size={12} withShadow={false} />
          )}
          <Text size='sm'>
            {[filament.type, fmtGrams(filament.grams)].filter(Boolean).join(' ')}
          </Text>
        </Group>
      ))}
    </Group>
  );
}

/** One line on what a linked file is: plate size, time, where it prints */
export function LinkFacts({ link }: Readonly<{ link: Link }>) {
  const facts = [
    `${fmtQty(link.parts_per_plate)} per plate`,
    fmtDuration(link.print_time_seconds),
    targetLabel(link)
  ].filter(Boolean);

  return (
    <Group gap='md' wrap='wrap'>
      <Text size='sm'>{facts.join(' · ')}</Text>
      <Filaments filaments={link.filaments} />
    </Group>
  );
}

/** Search box for a stock location */
export function LocationSelect({
  context,
  value,
  valueLabel,
  onChange,
  label,
  description,
  placeholder,
  error
}: Readonly<{
  context: InvenTreePluginContext;
  value: number | null;
  valueLabel?: string | null;
  onChange: (value: number | null) => void;
  label: string;
  description?: string;
  placeholder?: string;
  error?: string;
}>) {
  const api = useRef(context.api);
  api.current = context.api;

  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<{ value: string; label: string }[]>(
    value && valueLabel ? [{ value: String(value), label: valueLabel }] : []
  );

  useEffect(() => {
    let cancelled = false;

    const timer = window.setTimeout(() => {
      api.current
        .get('/api/stock/location/', {
          params: { search: search || undefined, limit: 30, ordering: 'pathstring' }
        })
        .then((response) => {
          if (cancelled) {
            return;
          }

          const found = listRows(response).map((row) => ({
            value: String(row.pk),
            label: String(row.pathstring ?? row.name)
          }));

          setOptions((current) => {
            // Keep the chosen location in the list, so that its name still shows
            const chosen = current.find((option) => option.value === String(value));

            return chosen && !found.some((option) => option.value === chosen.value)
              ? [chosen, ...found]
              : found;
          });
        })
        .catch(() => {
          // The list simply stays as it is
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [search, value]);

  return (
    <Select
      label={label}
      description={description}
      placeholder={placeholder ?? 'Search for a location'}
      data={options}
      value={value ? String(value) : null}
      onChange={(next) => onChange(next ? Number(next) : null)}
      searchable
      clearable
      onSearchChange={setSearch}
      filter={({ options: all }) => all}
      nothingFoundMessage='No location found'
      error={error}
      comboboxProps={{ withinPortal: true }}
    />
  );
}

/** Dialog: how many parts to print */
export function PrintModal({
  context,
  slug,
  part,
  link,
  suggested,
  opened,
  onClose,
  onDone
}: Readonly<{
  context: InvenTreePluginContext;
  slug: string;
  part: PartLabel;
  link: Link;
  suggested?: number;
  opened: boolean;
  onClose: () => void;
  onDone: () => void;
}>) {
  const perPlate = link.parts_per_plate;
  const [quantity, setQuantity] = useState<number | string>(perPlate);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only when the dialog opens: a refresh behind it must not reset the number
  const start = useRef(perPlate);
  start.current = suggested && suggested > 0 ? suggested : perPlate;

  useEffect(() => {
    if (opened) {
      setQuantity(start.current);
      setError(null);
    }
  }, [opened]);

  const wanted = typeof quantity === 'number' ? quantity : Number(quantity) || 0;
  const plates = platesFor(wanted, perPlate);
  const printed = plates * perPlate;

  const submit = () => {
    setBusy(true);
    setError(null);

    context.api
      .post(`/plugin/${slug}/part/${part.pk}/print/`, { plates })
      .then(() => {
        onDone();
        onClose();
      })
      .catch((err: any) => setError(apiError(err, 'Could not queue the print')))
      .finally(() => setBusy(false));
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Print ${part.name}`} centered>
      <Stack gap='md'>
        <NumberInput
          label='Parts needed'
          description={`${fmtQty(perPlate)} on a plate. Rounded up to whole plates.`}
          value={quantity}
          onChange={setQuantity}
          min={1}
          allowDecimal={false}
          data-autofocus
        />
        <Paper withBorder p='sm'>
          <Text fw={600}>
            {plural(plates, 'plate')}, {plural(printed, 'part')}
          </Text>
          <Text size='sm' c='dimmed'>
            {[
              link.print_time_seconds
                ? `${fmtDuration(link.print_time_seconds * plates)} of printing`
                : '',
              link.filament_grams ? `${fmtGrams(link.filament_grams * plates)} of filament` : '',
              targetLabel(link)
            ]
              .filter(Boolean)
              .join(' · ')}
          </Text>
        </Paper>
        <Text size='sm' c='dimmed'>
          A build order is raised for the {plural(printed, 'part')} and the plates are added to
          the Bambuddy queue. Nothing starts printing until the run is released.
        </Text>
        {error && <Alert color='red'>{error}</Alert>}
        <Group justify='flex-end'>
          <Button variant='default' onClick={onClose}>
            Cancel
          </Button>
          <Button
            leftSection={<IconPrinter size={16} />}
            onClick={submit}
            loading={busy}
            disabled={plates < 1}
          >
            Queue {plural(plates, 'plate')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function PlateChip({ item, index }: Readonly<{ item: QueueItem; index: number }>) {
  const staged = item.status === 'pending' && item.manual_start;
  const state = staged
    ? { label: 'Staged', color: 'violet' }
    : (ITEM_STATE[item.status] ?? { label: item.status, color: 'gray' });

  const lines = [
    item.printer_name ?? (item.target_model ? `Any ${item.target_model}` : ''),
    item.status === 'pending' && !staged && item.waiting_reason
      ? `Waiting: ${item.waiting_reason}`
      : '',
    item.filament_short ? 'The loaded filament may not be enough' : '',
    item.started_at ? `Started ${fmtDateTime(item.started_at)}` : '',
    item.completed_at ? `Finished ${fmtDateTime(item.completed_at)}` : '',
    item.error_message ?? '',
    item.counted ? 'Counted into stock' : '',
    `Bambuddy queue item ${item.id}`
  ].filter(Boolean);

  return (
    <Tooltip
      multiline
      label={
        <Stack gap={0}>
          {lines.map((line) => (
            <Text size='xs' key={line}>
              {line}
            </Text>
          ))}
        </Stack>
      }
    >
      <Badge
        color={state.color}
        variant={item.counted ? 'filled' : 'light'}
        radius='sm'
        leftSection={item.counted ? <IconCheck size={11} /> : undefined}
      >
        {index + 1} {state.label}
      </Badge>
    </Tooltip>
  );
}

type RunDialog = 'confirm' | 'reprint' | 'close' | 'cancel' | null;

/** One print run: its state, its plates and what can be done next */
export function RunCard({
  context,
  slug,
  run,
  permissions,
  settings,
  showPart,
  onChanged
}: Readonly<{
  context: InvenTreePluginContext;
  slug: string;
  run: Run;
  permissions: Permissions;
  settings: Settings;
  showPart?: boolean;
  onChanged: () => void;
}>) {
  const summary = run.summary;
  const state = RUN_STATE[summary.state] ?? RUN_STATE.queued;
  const active = summary.state !== 'done' && summary.state !== 'cancelled';

  const [dialog, setDialog] = useState<RunDialog>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<{ id: number; message: string; can_skip: boolean }[]>([]);

  const [good, setGood] = useState<number | string>(0);
  const [location, setLocation] = useState<number | null>(run.destination);
  const [notes, setNotes] = useState('');
  const [plates, setPlates] = useState<number | string>(1);

  const act = (action: string, body: Record<string, unknown> = {}) => {
    setBusy(action);
    setError(null);

    return context.api
      .post(`/plugin/${slug}/run/${run.build}/${action}/`, body)
      .then((response: any) => {
        const result = response.data?.result ?? {};

        setBlocked(action === 'release' ? (result.blocked ?? []) : []);

        if (action === 'cancel' && result.printing > 0) {
          setError(
            `${plural(result.printing, 'plate')} already printing: left to finish. Count the parts when it is done.`
          );
        }

        setDialog(null);
        onChanged();
      })
      .catch((err: any) => setError(apiError(err, 'That did not work')))
      .finally(() => setBusy(null));
  };

  const open = (which: RunDialog) => {
    setError(null);

    if (which === 'confirm') {
      setGood(summary.awaiting_quantity);
      setLocation(run.destination);
      setNotes('');
    }

    if (which === 'reprint') {
      setPlates(Math.max(1, summary.shortfall_plates));
    }

    setDialog(which);
  };

  const goodCount = typeof good === 'number' ? good : Number(good) || 0;
  const afterCount = Math.max(0, summary.remaining - goodCount);
  const stillOpen = (summary.counts.pending ?? 0) + (summary.counts.printing ?? 0);
  const plateCount = typeof plates === 'number' ? plates : Number(plates) || 0;

  const facts = [
    `${plural(run.items.length, 'plate')} × ${fmtQty(run.parts_per_plate)}`,
    targetLabel(run),
    run.print_time_seconds ? fmtDuration(run.print_time_seconds * run.items.length) : ''
  ].filter(Boolean);

  return (
    <Paper withBorder p='sm'>
      <Stack gap='xs'>
        <Group justify='space-between' align='flex-start' wrap='wrap' gap='xs'>
          <Stack gap={2}>
            <Group gap='xs' wrap='wrap'>
              <AppLink context={context} to={buildUrl(run.build)} title='Open the build order'>
                <Text fw={600} span>
                  {run.reference}
                </Text>
              </AppLink>
              <Tooltip label={state.hint} multiline w={260}>
                <Badge color={state.color} variant='light'>
                  {state.label}
                </Badge>
              </Tooltip>
              {showPart && (
                <AppLink context={context} to={partUrl(run.part)}>
                  {run.part_ipn ? `${run.part_ipn} · ${run.part_name}` : run.part_name}
                </AppLink>
              )}
            </Group>
            <Text size='sm'>
              {fmtQty(run.completed)} of {fmtQty(run.quantity)} in stock · {facts.join(' · ')}
            </Text>
            <Text size='xs' c='dimmed'>
              {SOURCE_LABEL[run.source] ?? run.source}
              {run.trigger_build ? ` (${run.trigger_build.reference})` : ''}
              {run.issued_by ? ` · ${run.issued_by}` : ''}
              {run.destination_name ? ` · to ${run.destination_name}` : ''}
            </Text>
          </Stack>
          {permissions.manage && active && (
            <Group gap='xs' wrap='wrap'>
              {summary.can_release && (
                <Button
                  size='xs'
                  color='violet'
                  leftSection={<IconPlayerPlay size={14} />}
                  loading={busy === 'release'}
                  onClick={() => act('release')}
                >
                  Release
                </Button>
              )}
              {summary.can_confirm && (
                <Button
                  size='xs'
                  color='orange'
                  leftSection={<IconCheck size={14} />}
                  onClick={() => open('confirm')}
                >
                  Count parts
                </Button>
              )}
              {summary.can_reprint && (
                <Button
                  size='xs'
                  variant='light'
                  leftSection={<IconPrinter size={14} />}
                  onClick={() => open('reprint')}
                >
                  {summary.state === 'failed' ? 'Print again' : 'Print the shortfall'}
                </Button>
              )}
              {summary.can_close && (
                <Button size='xs' variant='default' onClick={() => open('close')}>
                  Close short
                </Button>
              )}
              {summary.can_cancel && (
                <Button
                  size='xs'
                  variant='subtle'
                  color='red'
                  leftSection={<IconX size={14} />}
                  onClick={() => open('cancel')}
                >
                  Cancel
                </Button>
              )}
            </Group>
          )}
        </Group>

        <Group gap={6} wrap='wrap'>
          {run.items.map((item, index) => (
            <PlateChip item={item} index={index} key={item.id} />
          ))}
          {settings.public_url && active && (
            <Anchor href={`${settings.public_url}/queue`} target='_blank' rel='noreferrer' size='xs'>
              <Group gap={3} wrap='nowrap'>
                Bambuddy queue <IconExternalLink size={12} />
              </Group>
            </Anchor>
          )}
        </Group>

        {active && (
          <Text size='xs' c='dimmed'>
            {state.hint}
            {run.checked ? ` Queue read ${fmtDateTime(run.checked)}.` : ''}
          </Text>
        )}

        {blocked.length > 0 && (
          <Alert color='yellow' title='Bambuddy did not release every plate'>
            <Stack gap={4}>
              {blocked.map((entry) => (
                <Text size='sm' key={entry.id}>
                  {entry.message}
                </Text>
              ))}
              {blocked.some((entry) => entry.can_skip) && (
                <Group>
                  <Button
                    size='xs'
                    variant='default'
                    loading={busy === 'release'}
                    onClick={() => act('release', { skip_filament_check: true })}
                  >
                    Release anyway
                  </Button>
                </Group>
              )}
            </Stack>
          </Alert>
        )}

        {error && dialog === null && <Alert color='red'>{error}</Alert>}
      </Stack>

      <Modal
        opened={dialog === 'confirm'}
        onClose={() => setDialog(null)}
        title={`Count the parts of ${run.reference}`}
        centered
      >
        <Stack gap='md'>
          <Text size='sm'>
            {plural(summary.awaiting_plates, 'plate has', 'plates have')} finished printing: up to{' '}
            {plural(summary.awaiting_quantity, 'part')}. Enter how many are good. They are booked
            into stock against the build order.
          </Text>
          <NumberInput
            label='Good parts'
            value={good}
            onChange={setGood}
            min={0}
            max={summary.awaiting_quantity}
            allowDecimal={false}
            data-autofocus
          />
          <LocationSelect
            context={context}
            label='Into location'
            value={location}
            valueLabel={run.destination_name}
            onChange={setLocation}
            placeholder='Default location of the part'
          />
          <Textarea
            label='Note'
            placeholder='Optional'
            value={notes}
            onChange={(event) => setNotes(event.currentTarget.value)}
            autosize
            minRows={1}
          />
          {stillOpen > 0 ? (
            <Text size='sm' c='dimmed'>
              {plural(stillOpen, 'plate is', 'plates are')} still in the queue. The run stays open
              for {stillOpen === 1 ? 'it' : 'them'}.
            </Text>
          ) : afterCount > 0 ? (
            <Text size='sm' c='dimmed'>
              That leaves the build order {plural(afterCount, 'part')} short. Afterwards you can
              print the shortfall or close the run as it is.
            </Text>
          ) : (
            <Text size='sm' c='dimmed'>
              That completes the build order.
            </Text>
          )}
          {error && <Alert color='red'>{error}</Alert>}
          <Group justify='flex-end'>
            <Button variant='default' onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              color='orange'
              loading={busy === 'confirm'}
              onClick={() => act('confirm', { good: goodCount, location, notes })}
            >
              Book {plural(goodCount, 'part')} into stock
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={dialog === 'reprint'}
        onClose={() => setDialog(null)}
        title={`Print more for ${run.reference}`}
        centered
      >
        <Stack gap='md'>
          <Text size='sm'>
            The build order is still {plural(summary.remaining, 'part')} short. More plates are
            added to the same run, staged in the Bambuddy queue.
          </Text>
          <NumberInput
            label='Plates'
            description={`${fmtQty(run.parts_per_plate)} parts on a plate`}
            value={plates}
            onChange={setPlates}
            min={1}
            allowDecimal={false}
            data-autofocus
          />
          {error && <Alert color='red'>{error}</Alert>}
          <Group justify='flex-end'>
            <Button variant='default' onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              loading={busy === 'reprint'}
              disabled={plateCount < 1}
              onClick={() => act('reprint', { plates: plateCount })}
            >
              Queue {plural(plateCount, 'plate')}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={dialog === 'close'}
        onClose={() => setDialog(null)}
        title={`Close ${run.reference} short`}
        centered
      >
        <Stack gap='md'>
          <Text size='sm'>
            The build order is completed with the {plural(run.completed, 'part')} which are in
            stock, {plural(summary.remaining, 'part')} fewer than it asked for.
          </Text>
          {error && <Alert color='red'>{error}</Alert>}
          <Group justify='flex-end'>
            <Button variant='default' onClick={() => setDialog(null)}>
              Keep it open
            </Button>
            <Button loading={busy === 'close'} onClick={() => act('close')}>
              Close the run
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={dialog === 'cancel'}
        onClose={() => setDialog(null)}
        title={`Cancel ${run.reference}`}
        centered
      >
        <Stack gap='md'>
          <Text size='sm'>
            Plates which have not started are taken out of the Bambuddy queue. A plate which is
            printing is left to finish. The build order is cancelled if nothing was printed.
          </Text>
          {error && <Alert color='red'>{error}</Alert>}
          <Group justify='flex-end'>
            <Button variant='default' onClick={() => setDialog(null)}>
              Keep the run
            </Button>
            <Button color='red' loading={busy === 'cancel'} onClick={() => act('cancel')}>
              Cancel the run
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
}
