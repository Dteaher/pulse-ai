import type Modeler from 'bpmn-js/lib/Modeler';
import { recordBpmnImport } from './performance';

export async function checkImport(xml: string) {
  const started = performance.now();
  const container = document.createElement('div');
  let modeler: Modeler | undefined;
  try {
    const { default: BPMNModeler } = await import('bpmn-js/lib/Modeler');
    modeler = new BPMNModeler({ container });
    const { warnings } = await modeler.importXML(xml);
    if (warnings.length)
      throw new Error(
        'BPMN содержит неподдерживаемые элементы или потерянные ссылки. Импорт отменён: ' +
          'Проверьте ссылки и поддерживаемые элементы схемы.',
      );
  } catch (e) {
    throw new Error(
      'Не удалось импортировать BPMN. Проверьте XML и поддерживаемые элементы схемы.',
    );
  } finally {
    recordBpmnImport(started, 'check');
    modeler?.destroy();
  }
}
