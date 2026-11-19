import type { InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Badge,
  Button,
  Center,
  Group,
  Image,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { IconLink, IconPencil, IconPrinter, IconRefresh } from '@tabler/icons-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { LinkModal } from './LinkModal';
import { RUN_STATE, apiData, apiError, fmtDate, fmtQty, plural } from './format';
import type { Decision, Link, Numbers, PartData, Run, Settings } from './types';
import { AppLink, Figure, LinkFacts, PrintModal, RunCard, buildUrl } from './ui';

/** What the automatic check does for this part, in a sentence */
export function autoSentence(
  link: Link,
  numbers: Numbers,
  decision: Decision | null,
  settings: Settings
): { text: string; color?: string } {
  if (!link.auto) {
    return { text: 'Automatic printing is off for this part.' };
  }

  if (!settings.auto_low_stock && !settings.auto_build_demand) {
    return {
      text: 'Automatic printing is on for this part, but switched off in the plugin settings.',
      color: 'orange'
    };
  }

  const rules: string[] = [];

  if (settings.auto_low_stock) {
    rules.push(
      numbers.minimum > 0
        ? `stock falls below ${fmtQty(numbers.minimum)}${
            link.restock_to && link.restock_to > numbers.minimum
              ? ` (prints up to ${fmtQty(link.restock_to)})`
              : ''
          }`
        : 'stock falls below the minimum (none is set on this part)'
    );
  }

  if (settings.auto_build_demand) {
    rules.push('a build order in production needs more than there is');
  }

  let text = `Queued automatically when ${rules.join(', or when ')}.`;

  if (decision && decision.plates > 0) {
    text += ` The next check queues ${plural(decision.plates, 'plate')}.`;
  }

  return { text };
}

function HistoryTable({
  context,
  runs
}: Readonly<{ context: InvenTreePluginContext; runs: Run[] }>) {
  return (
    <Table striped withTableBorder verticalSpacing={6}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Build order</Table.Th>
          <Table.Th>Date</Table.Th>
          <Table.Th style={{ textAlign: 'right' }}>Plates</Table.Th>
          <Table.Th style={{ textAlign: 'right' }}>Good parts</Table.Th>
          <Table.Th>Result</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {runs.map((run) => {
          const state = RUN_STATE[run.summary.state] ?? RUN_STATE.done;

          return (
            <Table.Tr key={run.build}>
              <Table.Td>
                <AppLink context={context} to={buildUrl(run.build)}>
                  {run.reference}
                </AppLink>
              </Table.Td>
              <Table.Td>{fmtDate(run.created)}</Table.Td>
              <Table.Td style={{ textAlign: 'right' }}>{fmtQty(run.items.length)}</Table.Td>
              <Table.Td style={{ textAlign: 'right' }}>
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
  );
}

/** 3D Print panel on a part page */
export function BambuddyPartPanel({
  context
}: Readonly<{ context: InvenTreePluginContext }>) {
  const api = useRef(context.api);
  api.current = context.api;

  const partId: number | null = context.context?.part_id ?? context.id ?? null;
  const slug: string = context.context?.slug ?? 'bambuddy';

  const [data, setData] = useState<PartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [linking, setLinking] = useState(false);
  const [printing, setPrinting] = useState(false);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    if (partId === null || partId === undefined) {
      setLoading(false);
      setError('No part is associated with this page');
      return;
    }

    let cancelled = false;

    setLoading(true);
    setError(null);

    api.current
      .get(`/plugin/${slug}/part/${partId}/`)
      .then((response) => {
        if (!cancelled) {
          setData(apiData<PartData>(response, 'part'));
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(apiError(err, 'Could not load the 3D print data for this part'));
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
  }, [partId, slug, reloadKey]);

  // Follow open runs while the panel is on screen
  const hasOpenRuns = (data?.runs.length ?? 0) > 0;

  useEffect(() => {
    if (!hasOpenRuns) {
      return;
    }

    const timer = window.setInterval(reload, 30000);

    return () => window.clearInterval(timer);
  }, [hasOpenRuns, reload]);

  if (!data) {
    return error ? (
      <Alert color='red' title='Could not load the 3D print data'>
        {error}
      </Alert>
    ) : (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }

  const { part, link, numbers, decision, permissions, settings } = data;

  const modal = (
    <LinkModal
      context={context}
      slug={slug}
      part={part}
      link={link}
      settings={settings}
      opened={linking}
      onClose={() => setLinking(false)}
      onSaved={reload}
    />
  );

  if (!settings.configured) {
    return (
      <Alert color='yellow' title='Bambuddy is not set up yet'>
        Enter the address of the Bambuddy server in the settings of the Bambuddy plugin (Admin
        Center, Plugins).
      </Alert>
    );
  }

  if (!link) {
    return (
      <Stack gap='md' align='flex-start'>
        {error && <Alert color='red'>{error}</Alert>}
        <Text>
          This part is not linked to a file in Bambuddy. Link it to its sliced file to print it
          from here.
        </Text>
        {permissions.link ? (
          <Button leftSection={<IconLink size={16} />} onClick={() => setLinking(true)}>
            Link a sliced file
          </Button>
        ) : (
          <Text size='sm' c='dimmed'>
            You need permission to change parts to link it.
          </Text>
        )}
        {data.history.length > 0 && <HistoryTable context={context} runs={data.history} />}
        {modal}
      </Stack>
    );
  }

  const auto = autoSentence(link, numbers, decision, settings);
  const projected = numbers.stock + numbers.incoming - numbers.demand;
  const suggested = Math.max(0, numbers.minimum - projected, -projected);

  return (
    <Stack gap='md'>
      {error && <Alert color='red'>{error}</Alert>}
      {data.queue_error && (
        <Alert color='yellow' title='The Bambuddy queue could not be read'>
          {data.queue_error}. The runs below show what was last known.
        </Alert>
      )}

      <Paper withBorder p='sm'>
        <Group justify='space-between' align='flex-start' wrap='wrap'>
          <Group gap='md' wrap='nowrap' align='flex-start'>
            {link.has_thumbnail && (
              <Image
                src={`/plugin/${slug}/files/${link.file_id}/thumbnail/`}
                w={72}
                h={72}
                fit='contain'
                radius='sm'
                alt=''
              />
            )}
            <Stack gap={4}>
              <Text fw={600} style={{ wordBreak: 'break-all' }}>
                {link.filename}
                {link.plate_id ? ` · plate ${link.plate_id}` : ''}
              </Text>
              <LinkFacts link={link} />
              <Text size='sm' c={auto.color ?? 'dimmed'}>
                {auto.text}
              </Text>
            </Stack>
          </Group>
          <Group gap='xs'>
            <Button
              variant='default'
              size='xs'
              leftSection={<IconRefresh size={14} />}
              loading={loading}
              onClick={reload}
            >
              Refresh
            </Button>
            {permissions.link && (
              <Button
                variant='default'
                size='xs'
                leftSection={<IconPencil size={14} />}
                onClick={() => setLinking(true)}
              >
                Edit link
              </Button>
            )}
            {permissions.print && (
              <Button
                size='xs'
                leftSection={<IconPrinter size={14} />}
                onClick={() => setPrinting(true)}
              >
                Print
              </Button>
            )}
          </Group>
        </Group>
      </Paper>

      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <Figure
          label='In stock'
          value={fmtQty(numbers.stock)}
          color={numbers.minimum > 0 && numbers.stock < numbers.minimum ? 'orange' : undefined}
          hint={numbers.minimum > 0 ? `Minimum ${fmtQty(numbers.minimum)}` : 'No minimum set'}
        />
        <Figure
          label='Being printed'
          value={fmtQty(numbers.incoming)}
          hint={
            numbers.incoming > numbers.incoming_runs
              ? `${fmtQty(numbers.incoming - numbers.incoming_runs)} on other build orders`
              : 'On open print runs'
          }
        />
        <Figure
          label='Needed by build orders'
          value={fmtQty(numbers.demand)}
          hint={
            numbers.demand_builds.length > 0
              ? numbers.demand_builds
                  .slice(0, 3)
                  .map((build) => build.reference)
                  .join(', ') + (numbers.demand_builds.length > 3 ? ', ...' : '')
              : settings.include_pending
                ? 'Open build orders'
                : 'Build orders in production'
          }
        />
        <Figure
          label='Left over'
          value={fmtQty(projected)}
          color={projected < 0 ? 'red' : undefined}
          hint='In stock + being printed - needed'
        />
      </SimpleGrid>

      {data.runs.length > 0 ? (
        <Stack gap='xs'>
          <Title order={5}>Print runs</Title>
          {data.runs.map((run) => (
            <RunCard
              key={run.build}
              context={context}
              slug={slug}
              run={run}
              permissions={permissions}
              settings={settings}
              onChanged={reload}
            />
          ))}
        </Stack>
      ) : (
        <Text size='sm' c='dimmed'>
          No print run is open for this part.
        </Text>
      )}

      {data.history.length > 0 && (
        <Stack gap='xs'>
          <Title order={5}>Finished runs</Title>
          <HistoryTable context={context} runs={data.history} />
        </Stack>
      )}

      {modal}
      <PrintModal
        context={context}
        slug={slug}
        part={part}
        link={link}
        suggested={suggested}
        opened={printing}
        onClose={() => setPrinting(false)}
        onDone={reload}
      />
    </Stack>
  );
}
