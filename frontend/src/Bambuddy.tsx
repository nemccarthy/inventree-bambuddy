import type { InvenTreePluginContext } from '@inventreedb/ui';

import { BambuddyPage } from './Page';
import { BambuddyPartPanel } from './PartPanel';

/** The 3D Prints page, at /web/plugin/bambuddy/prints */
export function renderBambuddyPage(context: InvenTreePluginContext) {
  return <BambuddyPage context={context} />;
}

/** The 3D Print panel on a part page */
export function renderBambuddyPartPanel(context: InvenTreePluginContext) {
  return <BambuddyPartPanel context={context} />;
}
