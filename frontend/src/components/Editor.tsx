import { useEffect, useRef, forwardRef, useImperativeHandle } from 'react';
import Modeler from 'bpmn-js/lib/Modeler';
import { recordBpmnImport } from '../services/performance';
import translation from './translate';
import 'bpmn-js/dist/assets/diagram-js.css';
import 'bpmn-js/dist/assets/bpmn-js.css';
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn.css';

export interface EditorHandle {
  xml(): Promise<string>;
  undo(): void;
  redo(): void;
  fit(): void;
  zoom(delta: number): void;
  highlight(ids: string[]): void;
}
interface Props {
  xml: string;
  showTools: boolean;
  onSelect(id: string | null): void;
  onChange(): void;
  onError(message: string): void;
  onReady(ready: boolean): void;
}

export default forwardRef<EditorHandle, Props>(function Editor(
  { xml, showTools, onSelect, onChange, onError, onReady },
  ref,
) {
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<Modeler | null>(null);
  const callbacks = useRef({ onSelect, onChange, onError, onReady });
  callbacks.current = { onSelect, onChange, onError, onReady };
  const importing = useRef(false);
  useEffect(() => {
    const modeler = new Modeler({ container: host.current!, additionalModules: [translation] });
    instance.current = modeler;
    modeler.on('selection.changed', (e) =>
      callbacks.current.onSelect(e.newSelection[0]?.id ?? null),
    );
    modeler.on('commandStack.changed', () => {
      if (!importing.current) callbacks.current.onChange();
    });
    return () => {
      modeler.destroy();
      instance.current = null;
    };
  }, []);
  useEffect(() => {
    const modeler = instance.current!;
    let cancelled = false;
    importing.current = true;
    callbacks.current.onReady(false);
    const started = performance.now();
    modeler
      .importXML(xml)
      .then(({ warnings }) => {
        if (cancelled) return;
        fitCanvas(modeler);
        callbacks.current.onReady(true);
        if (warnings.length)
          callbacks.current.onError(
            'Импорт выполнен с предупреждениями: ' + warnings.map((w) => w.message).join('; '),
          );
      })
      .catch(() => {
        if (!cancelled)
          callbacks.current.onError('Не удалось открыть BPMN. Проверьте формат файла.');
      })
      .finally(() => {
        recordBpmnImport(started, 'render');
        if (!cancelled) importing.current = false;
      });
    return () => {
      cancelled = true;
    };
  }, [xml]);
  useImperativeHandle(
    ref,
    () => ({
      xml: async () => (await instance.current!.saveXML({ format: true })).xml,
      undo: () => instance.current?.get('commandStack').undo(),
      redo: () => instance.current?.get('commandStack').redo(),
      fit: () => {
        if (instance.current) fitCanvas(instance.current);
      },
      zoom: (delta) => {
        const canvas = instance.current?.get('canvas');
        if (canvas) canvas.zoom(Math.max(0.15, Math.min(2.5, canvas.zoom() + delta)));
      },
      highlight: (ids) => {
        const modeler = instance.current;
        if (!modeler) return;
        const registry = modeler.get('elementRegistry');
        const canvas = modeler.get('canvas');
        registry
          .getAll()
          .forEach((el: { id: string }) => canvas.removeMarker(el.id, 'audit-highlight'));
        const elements = ids.map((id) => registry.get(id)).filter(Boolean);
        elements.forEach((el) => canvas.addMarker(el.id, 'audit-highlight'));
        if (elements.length) {
          modeler.get('selection').select(elements[0]);
          canvas.scrollToElement(elements[0]);
        }
      },
    }),
    [],
  );
  return (
    <div
      className={'bpmn-canvas' + (showTools ? ' show-tools' : '')}
      ref={host}
      aria-label="Редактор BPMN"
    />
  );
});

function fitCanvas(modeler: Modeler) {
  const canvas = modeler.get('canvas');
  canvas.zoom('fit-viewport', 'auto');
  const view = canvas.viewbox();
  const inner = view.inner;
  const scale = Math.min(
    (view.outer.width - 64) / inner.width,
    (view.outer.height - 125) / inner.height,
    1,
  );
  canvas.viewbox({
    x: inner.x - 32 / scale,
    y: inner.y - 72 / scale,
    width: view.outer.width / scale,
    height: view.outer.height / scale,
  });
}
