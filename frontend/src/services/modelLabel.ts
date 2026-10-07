export function modelLabel(model: string) {
  return model.startsWith('gpt://')
    ? model.slice(6).split('/').slice(1).join('/')
    : (model.split('/').pop() ?? model).replace(/:free$/, '');
}
