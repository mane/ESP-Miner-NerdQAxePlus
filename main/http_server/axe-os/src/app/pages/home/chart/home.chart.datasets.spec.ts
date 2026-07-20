import {
  applyHomeDatasetTranslations,
  HOME_CHART_TRANSLATION_KEYS,
} from './home.chart.datasets';

describe('home chart dataset translations', () => {
  it('replaces every initial translation key after translations load', () => {
    const datasets = HOME_CHART_TRANSLATION_KEYS.map((key) => ({ label: key }));

    applyHomeDatasetTranslations(datasets, (key) => `translated:${key}`);

    expect(datasets.map((dataset) => String(dataset.label))).toEqual(
      HOME_CHART_TRANSLATION_KEYS.map((key): string => `translated:${key}`),
    );
  });

  it('is safe while chart data has not been initialized', () => {
    expect(() => applyHomeDatasetTranslations(undefined, (key) => key)).not.toThrow();
  });
});
