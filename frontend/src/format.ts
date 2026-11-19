import type { Link, Run, RunState } from './types';

const NO_API =
  'The Bambuddy plugin API was not found. Check that "Enable URL integration" is switched on in the InvenTree plugin settings.';

/**
 * Check that a response really came from the plugin API.
 *
 * With URL integration switched off, InvenTree answers unknown /plugin/
 * URLs with the web interface, so the request "succeeds" with a page of HTML.
 */
export function apiData<T>(response: any, field: string): T {
  const data = response?.data;

  if (!data || typeof data !== 'object' || !(field in data)) {
    throw Object.assign(new Error(NO_API), { noApi: true });
  }

  return data as T;
}

/** Turn an API error into a message for the user */
export function apiError(err: any, fallback: string): string {
  const status = err?.response?.status;
  const data = err?.response?.data;

  if (err?.noApi || (status === 404 && typeof data !== 'object')) {
    return NO_API;
  }

  if (status === 403) {
    return 'You do not have permission to do this.';
  }

  if (data && typeof data === 'object') {
    if (typeof data.detail === 'string') {
      return data.detail;
    }

    const messages: string[] = [];

    for (const value of Object.values(data)) {
      if (Array.isArray(value)) {
        messages.push(...value.map(String));
      } else if (typeof value === 'string') {
        messages.push(value);
      }
    }

    if (messages.length > 0) {
      return messages.join(' ');
    }
  }

  return `${fallback}${err?.message ? `: ${err.message}` : ''}`;
}

/** Messages for individual form fields, from a refused link */
export function fieldErrors(err: any): Record<string, string> {
  const errors = err?.response?.data?.errors;

  return errors && typeof errors === 'object' ? errors : {};
}

export function fmtQty(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '';
  }

  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function plural(count: number, one: string, many?: string): string {
  return `${fmtQty(count)} ${count === 1 ? one : (many ?? `${one}s`)}`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return '';
  }

  const date = new Date(iso);

  if (Number.isNaN(date.getTime())) {
    return '';
  }

  return date.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit'
  });
}

/** A date without a time (YYYY-MM-DD), shown as "23 Nov" */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) {
    return '';
  }

  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);

  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/** "1 h 30 min", "45 min" */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds <= 0) {
    return '';
  }

  const minutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;

  if (hours === 0) {
    return `${rest} min`;
  }

  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

export function fmtGrams(grams: number | null | undefined): string {
  if (grams === null || grams === undefined || grams <= 0) {
    return '';
  }

  return grams >= 1000
    ? `${(grams / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })} kg`
    : `${grams.toLocaleString(undefined, { maximumFractionDigits: 1 })} g`;
}

export function platesFor(quantity: number, perPlate: number): number {
  if (!(quantity > 0) || !(perPlate > 0)) {
    return 0;
  }

  return Math.ceil(quantity / perPlate - 1e-9);
}

/** Where a link or a run prints: "P1S-01", "Any P1S", "Any P1S in Workshop" */
export function targetLabel(
  target: Pick<Link, 'printer_id' | 'printer_name' | 'target_model' | 'target_location'> | Run
): string {
  if (target.printer_name) {
    return target.printer_name;
  }

  if ('printer_id' in target && target.printer_id) {
    return `Printer ${target.printer_id}`;
  }

  if (target.target_model) {
    return target.target_location
      ? `Any ${target.target_model} in ${target.target_location}`
      : `Any ${target.target_model}`;
  }

  return 'No printer chosen';
}

export const RUN_STATE: Record<RunState, { label: string; color: string; hint: string }> = {
  staged: {
    label: 'Waiting for release',
    color: 'violet',
    hint: 'In the Bambuddy queue. Nothing prints until it is released.'
  },
  queued: {
    label: 'Queued',
    color: 'blue',
    hint: 'Released. Bambuddy starts it when a printer is free.'
  },
  printing: { label: 'Printing', color: 'cyan', hint: 'On a printer now.' },
  printed: {
    label: 'Count the parts',
    color: 'orange',
    hint: 'Printing has finished. Count the good parts to book them into stock.'
  },
  short: {
    label: 'Short',
    color: 'yellow',
    hint: 'Fewer good parts than the build order asked for. Print the shortfall, or close the run.'
  },
  failed: {
    label: 'Nothing printed',
    color: 'red',
    hint: 'Every plate failed or was removed from the queue. Print again, or cancel the run.'
  },
  done: { label: 'Done', color: 'green', hint: 'The parts are in stock.' },
  cancelled: { label: 'Cancelled', color: 'gray', hint: 'The run was cancelled.' }
};

export const ITEM_STATE: Record<string, { label: string; color: string }> = {
  pending: { label: 'Queued', color: 'blue' },
  printing: { label: 'Printing', color: 'cyan' },
  completed: { label: 'Printed', color: 'green' },
  failed: { label: 'Failed', color: 'red' },
  skipped: { label: 'Skipped', color: 'red' },
  cancelled: { label: 'Cancelled', color: 'gray' },
  removed: { label: 'Removed', color: 'gray' }
};

export const SOURCE_LABEL: Record<string, string> = {
  manual: 'Queued by hand',
  low_stock: 'Queued automatically: stock was low',
  build_demand: 'Queued automatically: a build order needed parts'
};
