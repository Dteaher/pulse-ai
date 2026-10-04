declare module 'bpmn-js/lib/Modeler' {
  export default class Modeler {
    constructor(options: {
      container: HTMLElement;
      keyboard?: object;
      additionalModules?: object[];
    });
    importXML(xml: string): Promise<{ warnings: Error[] }>;
    saveXML(options: { format: boolean }): Promise<{ xml: string }>;
    destroy(): void;
    on(event: string, callback: (event: any) => void): void;
    get<T = any>(name: string): T;
  }
}
