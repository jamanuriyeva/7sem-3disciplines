# Генеративно-состязательные сети

Каталог содержит searchable-фрагмент учебного материала о GAN, подробную
лабораторную методичку и шесть Jupyter-ноутбуков. Первые два закрепляют dense
GAN на MNIST и Fashion-MNIST. Четыре новых ноутбука переносят работу на лица и
сравнивают dense GAN с RGB DCGAN в TensorFlow и PyTorch.

| Файл | Назначение |
|---|---|
| «lecture_gan_excerpt.pdf» | Точный фрагмент страниц 125–138 исходного PDF с сохранённым OCR-слоем |
| «lab_gan.md», «lab_gan.pdf» | Методичка лабораторной работы |
| «01_dense_gan_mnist_example.ipynb» | Выполненный пример на MNIST |
| «02_dense_gan_fashion_mnist_assignment.ipynb» | Самостоятельный вариант на Fashion-MNIST |
| «03_face_gan_tensorflow_example.ipynb» | Выполненное CPU-сравнение dense GAN и RGB DCGAN на LFW |
| «04_face_gan_tensorflow_assignment.ipynb» | Самостоятельный RGB DCGAN на Olivetti Faces в TensorFlow |
| «05_face_gan_pytorch_example.ipynb» | Выполненное сравнение GAN на LFW в PyTorch с Intel Arc XPU/CPU |
| «06_face_gan_pytorch_assignment.ipynb» | Самостоятельный RGB DCGAN на Olivetti Faces в PyTorch XPU/CPU |
| «assets/figures/manifest.md» | Служебный реестр происхождения схем |
| «assets/diagrams/» | Редактируемые PlantUML-инфографики потоков данных и автоматического дифференцирования |

## Среда и запуск

Нужен Python 3.13. TensorFlow-ноутбуки рассчитаны на CPU и используют
современный TensorFlow/Keras.

~~~bash
cd 03-gan
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/jupyter lab
~~~

Полный пример использует первые 10 000 изображений MNIST и выполняет 105 эпох.
В самостоятельном варианте те же константы можно уменьшить для первоначальной
отладки, но в отчёте следует указать фактический режим.

## Лицевые GAN и Intel Arc

Лицевые примеры используют цветной LFW, самостоятельные варианты — Olivetti
Faces. Метки персон не передаются в GAN и служат только для диагностики
загрузки данных. LFW, полученный через scikit-learn, уже находится в диапазоне
`[0, 1]`; перед обучением он преобразуется в `[-1, 1]` формулой `2 * x - 1`.
В TensorFlow-примере предусмотрен CPU-профиль: 2 500 лиц и 80 эпох (обычно
около 2,5 часов для обеих архитектур). Dense GAN служит базовой линией, а
DCGAN использует пространственную структуру изображения. PyTorch-пример
выбирает Intel Arc XPU при доступности и в этом случае
использует 8 000 лиц и 120 эпох; иначе он прозрачно возвращается к CPU-профилю.

Для PyTorch/XPU создаётся отдельная среда, не конфликтующая с TensorFlow:

~~~bash
cd 03-gan
./setup-pytorch-xpu.sh
.venv-xpu/bin/jupyter lab
~~~

Сценарий после установки выводит результат `torch.xpu.is_available()` и
минимальный тест тензора на Intel Arc. При значении `False` проверьте Intel
Compute Runtime, Level Zero и доступ к `/dev/dri/renderD128`; сами ноутбуки
всё равно остаются исполнимыми на CPU.

## Учебная инфографика

В Markdown-ячейки встроены готовые SVG-схемы двух фаз GAN, `GradientTape` и
PyTorch Autograd. Их исходники PlantUML находятся в `assets/diagrams/`.
Чтобы изменить схему и заново получить SVG, достаточно выполнить:

~~~bash
./render-plantuml-diagrams.sh
~~~

Сценарий использует установленную Java, автоматически скачивает в
пользовательский кэш закреплённый PlantUML с проверкой SHA-256 и не требует
Graphviz. Переменная `PLANTUML_JAR` позволяет явно указать уже проверенный JAR.

## PDF

Методичка собирается общим конвейером этого проекта из каталога «pdf».
По умолчанию он подписывает результат PAdES:

~~~bash
../pdf/build-lab-pdf.sh lab_gan.md lab_gan.pdf "Лабораторная работа «Генеративно-состязательные сети»"
~~~

Для локальной проверки без PAdES-подписи передайте `--no-sign` первым
аргументом. Обычный вызов по-прежнему подписывает PDF по умолчанию.
