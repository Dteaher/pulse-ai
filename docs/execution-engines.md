# Этап B1: сравнение движков

Проверено по официальным материалам 15.10.2026. Конкретный движок не выбран. Это сравнение возможностей поставщиков, а не проверка совместимости PULSE.

| Критерий | Camunda 7 | Camunda 8 | Flowable OSS |
|---|---|---|---|
| Лицензия | Community: Apache 2.0; Enterprise коммерческая [1] | Основные компоненты: Camunda License v1 для исходников, проприетарная для бинарных сборок; production требует Enterprise [2] | OSS движки: Apache 2.0; коммерческие продукты отдельно [3] |
| Сопровождение | CE maintenance завершено 14.10.2025; Enterprise до апреля 2030 [4] | Текущая линейка; закрепить версию и политику обновлений | Java engine; сопровождение приложения, БД и обновлений остаётся за проектом |
| Локальный Docker | Официальные образы [5] | Developer Docker Compose; не production-проект [6] | Официальный OSS REST-образ [3] |
| Условия | UEL/JUEL [7] | FEEL [8] | UEL [9] |
| Сообщения | Correlation API, business key и переменные [10] | Message name и correlation key; отдельная семантика старта [11] | RuntimeService, поиск подписки и messageEventReceived; транспорт реализуется приложением [9] |
| Автотесты | ProcessEngineRule / JUnit 5 ProcessEngineExtension [12] | Camunda Process Test, Docker/Testcontainers или remote runtime [13] | JUnit Jupiter FlowableExtension [14] |
| Интеграция с Python PULSE | REST и внешние workers; Java-контур отдельно | REST/клиенты, workers, компоненты платформы отдельно | REST или Java/Spring сервис; задачи и workers интегрируются отдельно |
| Сложность для нас | Средняя интеграция, высокий риск неподдерживаемой CE | Выше для полного self-managed контура; учитывать production-лицензию | Средняя для небольшого REST-стенда; интеграции не автоматические |

Оценки сложности — инженерная оценка, не benchmark. Для всех движков нужны конкретный профиль и испытания. Изменение isExecutable на true недостаточно.

Варианты для решения пользователем: Flowable OSS — рассмотреть для небольшого открытого стенда; Camunda 8 — при потребности в её экосистеме и приемлемой лицензии; Camunda 7 — при существующем контуре или Enterprise. Это предложение, не самостоятельный выбор.

## Официальные источники

1. [Camunda 7 licenses](https://docs.camunda.org/manual/7.24/introduction/licenses/)
2. [Camunda 8 licenses](https://docs.camunda.io/docs/reference/licenses/)
3. [Flowable OSS license and Docker](https://www.flowable.com/open-source-code)
4. [Camunda support](https://docs.camunda.org/enterprise/announcement/)
5. [Camunda 7 Docker](https://github.com/camunda/docker-camunda-bpm-platform/blob/7.24/README.md)
6. [Camunda 8 Docker Compose](https://docs.camunda.io/docs/8.8/self-managed/quickstart/developer-quickstart/docker-compose/)
7. [Camunda 7 expressions](https://docs.camunda.org/get-started/quick-start/gateway/)
8. [Camunda 8 FEEL](https://docs.camunda.io/docs/8.9/reference/glossary/)
9. [Flowable BPMN](https://www.flowable.com/open-source/docs/bpmn/ch07b-BPMN-Constructs/)
10. [Camunda 7 messages](https://docs.camunda.org/manual/7.24/reference/bpmn20/events/message-events/)
11. [Camunda 8 messages](https://docs.camunda.io/docs/8.9/components/concepts/messages/)
12. [Camunda 7 test helpers](https://docs.camunda.org/javadoc/camunda-bpm-platform/7.24/org/camunda/bpm/engine/class-use/ProcessEngineServices.html)
13. [Camunda Process Test](https://docs.camunda.io/docs/8.9/apis-tools/testing/getting-started/)
14. [FlowableExtension](https://www.flowable.com/open-source/docs/all-javadocs/index-all.html)
