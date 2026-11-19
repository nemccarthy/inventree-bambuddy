import type { InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip
} from '@mantine/core';
import {
  IconLink,
  IconPencil,
  IconPlayerPlay,
  IconPrinter,
  IconRefresh
} from '@tabler/icons-react';
import { type CSSProperties, useCallback, useEffect, useRef, useState } from 'react';

import { LinkModal } from './LinkModal';
import { autoSentence } from './PartPanel';
import {
  RUN_STATE,
  apiData,
  apiError,
  fmtDate,
  fmtDuration,
  fmtQty,
  plural,
  targetLabel
} from './format';
import type {
  CheckResult,
  ConfigData,
  OverviewData,
  OverviewPart,
  PartLabel
} from './types';
import { AppLink, Figure, PrintModal, RunCard, buildUrl, listRows, partUrl } from './ui';

const numCell: CSSProperties = {
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
  whiteSpace: 'nowrap'
};

/** Dialog: find a part to link */
function PartPicker({
  context,
  opened,
  onClose,
  onPicked
}: Readonly<{
  context: InvenTreePluginContext;
  opened: boolean;
  onClose: () => void;
  onPicked: (part: PartLabel) => void;
}>) {
  const api = useRef(context.api);
  api.current = context.api;

  const [search, setSearch] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [value, setValue] = useState<string | null>(null);

  useEffect(() => {
    if (!opened) {
      return;
    }

    let cancelled = false;

    const timer = window.setTimeout(() => {
      api.current
        .get('/api/part/', {
          params: { search: search || undefined, limit: 30, active: true, virtual: false }
        })
        .then((response) => {
          if (!cancelled) {
            setRows(listRows(response));
          }
        })
        .catch(() => {
          // The list simply stays as it is
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [opened, search]);

  useEffect(() => {
    if (opened) {
      setValue(null);
      setSearch('');
    }
  }, [opened]);

  const pick = () => {
    const row = rows.find((entry) => String(entry.pk) === value);

    if (!row) {
      return;
    }

    onPicked({
      pk: row.pk,
      name: row.name,
      ipn: row.IPN ?? '',
      description: row.description ?? '',
      units: row.units ?? '',
      active: row.active,
      assembly: row.assembly,
      trackable: row.trackable,
      minimum_stock: Number(row.minimum_stock ?? 0),
      default_location: row.default_location ?? null
    });
  };

  return (
    <Modal opened={opened} onClose={onClose} title='Link a part' centered>
      <Stack gap='md'>
        <Select
          label='Part'
          placeholder='Search by name or IPN'
          data={rows.map((row) => ({
            value: String(row.pk),
            label: row.IPN ? `${row.IPN} · ${row.full_name ?? row.name}` : (row.full_name ?? row.name)
          }))}
          value={value}
          onChange={setValue}
          searchable
          onSearchChange={setSearch}
          filter={({ options }) => options}
          nothingFoundMessage='No part found'
          comboboxProps={{ withinPortal: true }}
          data-autofocus
        />
        <Group justify='flex-end'>
          <Button variant='default' onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={value === null} onClick={pick}>
            Choose the file
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function autoCell(row: OverviewPart, data: OverviewData) {
  const sentence = autoSentence(row.link, row.numbers, row.decision, data.settings);

  if (!row.link.auto) {
    return (
      <Text size='sm' c='dimmed'>
        Off
      </Text>
    );
  }

  if (!row.decision.enabled) {
    return (
      <Tooltip label={sentence.text} multiline w={280}>
        <Badge color='gray' variant='light'>
          On, not active
        </Badge>
      </Tooltip>
    );
  }

  return (
    <Tooltip label={sentence.text} multiline w={280}>
      {row.decision.plates > 0 ? (
        <Badge color='orange' variant='light'>
          {plural(row.decision.plates, 'plate')} due
        </Badge>
      ) : (
        <Badge color='green' variant='light'>
          On
        </Badge>
      )}
    </Tooltip>
  );
}

/** The 3D Prints page: every linked part and every open print run */
export function BambuddyPage({ context }: Readonly<{ context: InvenTreePluginContext }>) {
  const api = useRef(context.api);
  api.current = context.api;

  const slug = 'bambuddy';

  const [data, setData] = useState<OverviewData | null>(null);
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [picking, setPicking] = useState(false);
  const [linkPart, setLinkPart] = useState<PartLabel | null>(null);
  const [linkRow, setLinkRow] = useState<OverviewPart | null>(null);
  const [printRow, setPrintRow] = useState<OverviewPart | null>(null);

  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    let cancelled = false;

    setLoading(true);
    setError(null);

    api.current
      .get(`/plugin/${slug}/overview/`)
      .then((response) => {
        if (!cancelled) {
          setData(apiData<OverviewData>(response, 'parts'));
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(apiError(err, 'Could not load the print runs'));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // The state of the Bambuddy server: once, and again on Refresh
  useEffect(() => {
    let cancelled = false;

    api.current
      .get(`/plugin/${slug}/config/`)
      .then((response) => {
        if (!cancelled) {
          setConfig(apiData<ConfigData>(response, 'bambuddy'));
        }
      })
      .catch(() => {
        // The overview request reports the problem
      });

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const hasOpenRuns = (data?.runs.length ?? 0) > 0;

  useEffect(() => {
    if (!hasOpenRuns) {
      return;
    }

    const timer = window.setInterval(reload, 30000);

    return () => window.clearInterval(timer);
  }, [hasOpenRuns, reload]);

  const runCheck = () => {
    setChecking(true);
    setCheckError(null);
    setCheckResult(null);

    api.current
      .post(`/plugin/${slug}/check/`, {})
      .then((response: any) => {
        setCheckResult(apiData<CheckResult>(response, 'results'));
        reload();
      })
      .catch((err: any) => setCheckError(apiError(err, 'The check did not run')))
      .finally(() => setChecking(false));
  };

  if (!data) {
    return error ? (
      <Alert color='red' title='Could not load the print runs' m='md'>
        {error}
      </Alert>
    ) : (
      <Center h={300}>
        <Loader />
      </Center>
    );
  }

  const { settings, permissions } = data;
  const server = config?.bambuddy;
  const autoOn = settings.auto_low_stock || settings.auto_build_demand;

  const waiting = data.runs.filter((run) => run.summary.state === 'staged').length;
  const toCount = data.runs.filter((run) => run.summary.can_confirm).length;
  const printing = data.runs.filter(
    (run) => run.summary.state === 'printing' || run.summary.state === 'queued'
  ).length;
  const due = data.parts.filter((row) => row.decision.enabled && row.decision.plates > 0).length;

  const background: string[] = [];

  if (!settings.schedule_enabled) {
    background.push('"Enable schedule integration"');
  }

  if (!settings.events_enabled) {
    background.push('"Enable event integration"');
  }

  return (
    <Stack gap='md' p='md'>
      <Group justify='space-between' wrap='wrap'>
        <Group gap='sm'>
          <Title order={3}>3D Prints</Title>
          {server &&
            (server.ok ? (
              <Badge color='green' variant='light'>
                Bambuddy {server.version ?? ''} connected
              </Badge>
            ) : (
              <Badge color='red' variant='light'>
                Bambuddy not reachable
              </Badge>
            ))}
        </Group>
        <Group gap='xs'>
          {permissions.print && autoOn && (
            <Button
              variant='default'
              leftSection={<IconPlayerPlay size={16} />}
              loading={checking}
              onClick={runCheck}
            >
              Check stock now
            </Button>
          )}
          {permissions.link && (
            <Button
              variant='default'
              leftSection={<IconLink size={16} />}
              onClick={() => setPicking(true)}
            >
              Link a part
            </Button>
          )}
          <Button
            variant='default'
            leftSection={<IconRefresh size={16} />}
            loading={loading}
            onClick={reload}
          >
            Refresh
          </Button>
        </Group>
      </Group>

      {error && <Alert color='red'>{error}</Alert>}

      {server && !server.ok && (
        <Alert color='red' title='Bambuddy could not be reached'>
          <Text size='sm'>{server.error}</Text>
          <Text size='sm' mt={4}>
            Print runs cannot be queued or followed until it can. The address and the API key
            are in the settings of the Bambuddy plugin.
          </Text>
        </Alert>
      )}

      {server?.ok && server.outdated && (
        <Alert color='yellow' title={`Bambuddy ${server.version} is older than this plugin expects`}>
          Print runs still queue, but they are not tagged with the build order reference in
          Bambuddy. Version 1.2.5.7 or later does that.
        </Alert>
      )}

      {data.queue_error && (
        <Alert color='yellow' title='The Bambuddy queue could not be read'>
          <Text size='sm'>{data.queue_error}</Text>
          <Text size='sm' mt={4}>
            The runs below show what was last known.
          </Text>
        </Alert>
      )}

      {background.length > 0 && (
        <Alert color='yellow' title='Background tasks are switched off in InvenTree'>
          Switch on {background.join(' and ')} in the InvenTree plugin settings. Without them the
          print queue is only read while this page is open
          {autoOn ? ', and nothing is queued automatically' : ''}.
        </Alert>
      )}

      {checkError && (
        <Alert color='red' withCloseButton onClose={() => setCheckError(null)}>
          {checkError}
        </Alert>
      )}

      {checkResult && (
        <Alert
          color={checkResult.results.some((entry) => entry.error) ? 'yellow' : 'blue'}
          title='Stock check'
          withCloseButton
          onClose={() => setCheckResult(null)}
        >
          {checkResult.results.length === 0 ? (
            <Text size='sm'>Nothing needs printing.</Text>
          ) : (
            <Stack gap={2}>
              {checkResult.results.map((entry) => (
                <Text size='sm' key={entry.part}>
                  {entry.name}:{' '}
                  {entry.error
                    ? `could not be queued. ${entry.error}`
                    : `${plural(entry.plates, 'plate')} queued as ${entry.reference}${
                        entry.capped ? ' (limited by the most plates at once)' : ''
                      }`}
                </Text>
              ))}
            </Stack>
          )}
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <Figure
          label='Waiting for release'
          value={fmtQty(waiting)}
          color={waiting > 0 ? 'violet' : undefined}
          hint='Runs staged in Bambuddy'
        />
        <Figure label='Queued or printing' value={fmtQty(printing)} hint='Runs on the printers' />
        <Figure
          label='To count'
          value={fmtQty(toCount)}
          color={toCount > 0 ? 'orange' : undefined}
          hint='Runs with finished plates'
        />
        <Figure
          label='Due to print'
          value={fmtQty(due)}
          color={due > 0 ? 'orange' : undefined}
          hint={autoOn ? 'Parts the next check queues' : 'Automatic printing is off'}
        />
      </SimpleGrid>

      <Stack gap='xs'>
        <Title order={5}>Print runs</Title>
        {data.runs.length === 0 ? (
          <Text size='sm' c='dimmed'>
            No print run is open.
          </Text>
        ) : (
          data.runs.map((run) => (
            <RunCard
              key={run.build}
              context={context}
              slug={slug}
              run={run}
              permissions={permissions}
              settings={settings}
              showPart
              onChanged={reload}
            />
          ))
        )}
      </Stack>

      <Stack gap='xs'>
        <Title order={5}>Linked parts</Title>
        {data.parts.length === 0 ? (
          <Text size='sm' c='dimmed'>
            No part is linked to a Bambuddy file yet. Use "Link a part", or the 3D Print panel on
            a part page.
          </Text>
        ) : (
          <ScrollArea>
            <Table striped withTableBorder verticalSpacing={6} miw={900}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Part</Table.Th>
                  <Table.Th>File</Table.Th>
                  <Table.Th style={numCell}>In stock</Table.Th>
                  <Table.Th style={numCell}>Minimum</Table.Th>
                  <Table.Th style={numCell}>Being printed</Table.Th>
                  <Table.Th style={numCell}>Needed</Table.Th>
                  <Table.Th>Automatic</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.parts.map((row) => (
                  <Table.Tr key={row.part.pk}>
                    <Table.Td>
                      <AppLink context={context} to={partUrl(row.part.pk)}>
                        {row.part.name}
                      </AppLink>
                      {row.part.ipn && (
                        <Text size='xs' c='dimmed'>
                          {row.part.ipn}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size='sm' style={{ wordBreak: 'break-all' }}>
                        {row.link.filename}
                      </Text>
                      <Text size='xs' c='dimmed'>
                        {[
                          `${fmtQty(row.link.parts_per_plate)} per plate`,
                          fmtDuration(row.link.print_time_seconds),
                          targetLabel(row.link)
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </Text>
                    </Table.Td>
                    <Table.Td style={numCell}>
                      <Text
                        size='sm'
                        span
                        c={
                          row.numbers.minimum > 0 && row.numbers.stock < row.numbers.minimum
                            ? 'orange'
                            : undefined
                        }
                        fw={
                          row.numbers.minimum > 0 && row.numbers.stock < row.numbers.minimum
                            ? 600
                            : undefined
                        }
                      >
                        {fmtQty(row.numbers.stock)}
                      </Text>
                    </Table.Td>
                    <Table.Td style={numCell}>
                      {row.numbers.minimum > 0 ? fmtQty(row.numbers.minimum) : ''}
                    </Table.Td>
                    <Table.Td style={numCell}>
                      {row.numbers.incoming > 0 ? fmtQty(row.numbers.incoming) : ''}
                    </Table.Td>
                    <Table.Td style={numCell}>
                      {row.numbers.demand > 0 ? fmtQty(row.numbers.demand) : ''}
                    </Table.Td>
                    <Table.Td>{autoCell(row, data)}</Table.Td>
                    <Table.Td>
                      <Group gap={6} justify='flex-end' wrap='nowrap'>
                        {permissions.link && (
                          <Button
                            size='compact-sm'
                            variant='default'
                            leftSection={<IconPencil size={13} />}
                            onClick={() => setLinkRow(row)}
                          >
                            Link
                          </Button>
                        )}
                        {permissions.print && (
                          <Button
                            size='compact-sm'
                            leftSection={<IconPrinter size={13} />}
                            onClick={() => setPrintRow(row)}
                          >
                            Print
                          </Button>
                        )}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Stack>

      {data.history.length > 0 && (
        <Stack gap='xs'>
          <Title order={5}>Finished runs</Title>
          <ScrollArea>
            <Table striped withTableBorder verticalSpacing={6} miw={700}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Build order</Table.Th>
                  <Table.Th>Part</Table.Th>
                  <Table.Th>Date</Table.Th>
                  <Table.Th style={numCell}>Plates</Table.Th>
                  <Table.Th style={numCell}>Good parts</Table.Th>
                  <Table.Th>Result</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.history.map((run) => {
                  const state = RUN_STATE[run.summary.state] ?? RUN_STATE.done;

                  return (
                    <Table.Tr key={run.build}>
                      <Table.Td>
                        <AppLink context={context} to={buildUrl(run.build)}>
                          {run.reference}
                        </AppLink>
                      </Table.Td>
                      <Table.Td>
                        <AppLink context={context} to={partUrl(run.part)}>
                          {run.part_name}
                        </AppLink>
                      </Table.Td>
                      <Table.Td>{fmtDate(run.created)}</Table.Td>
                      <Table.Td style={numCell}>{fmtQty(run.items.length)}</Table.Td>
                      <Table.Td style={numCell}>
                        {fmtQty(run.completed)} of {fmtQty(run.quantity)}
                      </Table.Td>
                      <Table.Td>
                        <Badge color={state.color} variant='light'>
                          {state.label}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </Stack>
      )}

      <PartPicker
        context={context}
        opened={picking}
        onClose={() => setPicking(false)}
        onPicked={(part) => {
          const row = data.parts.find((entry) => entry.part.pk === part.pk);

          setPicking(false);

          // A part which is linked already opens with its link
          if (row) {
            setLinkRow(row);
          } else {
            setLinkPart(part);
          }
        }}
      />

      {(linkPart || linkRow) && (
        <LinkModal
          context={context}
          slug={slug}
          part={(linkRow?.part ?? linkPart) as PartLabel}
          link={linkRow?.link ?? null}
          settings={settings}
          opened
          onClose={() => {
            setLinkPart(null);
            setLinkRow(null);
          }}
          onSaved={reload}
        />
      )}

      {printRow && (
        <PrintModal
          context={context}
          slug={slug}
          part={printRow.part}
          link={printRow.link}
          suggested={Math.max(
            0,
            printRow.numbers.minimum -
              (printRow.numbers.stock + printRow.numbers.incoming - printRow.numbers.demand),
            -(printRow.numbers.stock + printRow.numbers.incoming - printRow.numbers.demand)
          )}
          opened
          onClose={() => setPrintRow(null)}
          onDone={reload}
        />
      )}
    </Stack>
  );
}
