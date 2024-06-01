document.addEventListener('DOMContentLoaded', function () {
    let tpcCount = 0;
    let clickValue = 1;
    let autoClickerInterval = null;
    let upgradePrices = {
        clickValue: 10,
        autoClicker: 100
    };

    // Load saved coins, click value, and upgrade prices from localStorage
    if (localStorage.getItem('tpcCount')) {
        tpcCount = parseInt(localStorage.getItem('tpcCount'));
        updateCoins();
    }

    if (localStorage.getItem('clickValue')) {
        clickValue = parseInt(localStorage.getItem('clickValue'));
    }

    if (localStorage.getItem('upgradePrices')) {
        upgradePrices = JSON.parse(localStorage.getItem('upgradePrices'));
    }

    const shopButton = document.getElementById('shop-button');
    const shop = document.getElementById('shop');
    const resetClicksButton = document.getElementById('reset-clicks-button');

    shopButton.addEventListener('click', function () {
        toggleShop();
    });

    resetClicksButton.addEventListener('click', function () {
        tpcCount = 0;
        updateCoins();
        saveCoins();
    });

    function toggleShop() {
        shop.classList.toggle('hidden');
    }

    document.getElementById('mine-button').addEventListener('click', () => {
        tpcCount += clickValue;
        updateCoins();
        saveCoins();
        createCoinEffect();
    });

    function createCoinEffect() {
        const coinEffect = document.createElement('div');
        coinEffect.className = 'coin-effect';
        const miningArea = document.getElementById('mining-area');
        miningArea.appendChild(coinEffect);

        const effectSize = 30;
        const areaWidth = miningArea.clientWidth - effectSize;
        const areaHeight = miningArea.clientHeight - effectSize;
        coinEffect.style.left = `${Math.random() * areaWidth}px`;
        coinEffect.style.top = `${Math.random() * areaHeight}px`;

        setTimeout(() => {
            miningArea.removeChild(coinEffect);
        }, 1000);
    }

    function updateCoins() {
        document.getElementById('TPC').textContent = formatNumber(tpcCount);
    }

    function saveCoins() {
        localStorage.setItem('tpcCount', tpcCount);
    }

    function formatNumber(number) {
        if (number >= 1000000000) {
            return (number / 1000000000).toFixed(1) + 'B';
        } else if (number >= 1000000) {
            return (number / 1000000).toFixed(1) + 'M';
        } else {
            return number.toString();
        }
    }

    function updateUpgradePrices() {
        document.querySelectorAll('.item-cost').forEach((element, index) => {
            element.textContent = `Cost: ${upgradePrices[Object.keys(upgradePrices)[index]]} TPC`;
        });
    }

    const buyButtons = document.querySelectorAll('.buy-button');
    buyButtons.forEach(button => {
        button.addEventListener('click', function () {
            const upgrade = this.getAttribute('data-upgrade');
            const cost = upgradePrices[upgrade];
            if (tpcCount >= cost) {
                tpcCount -= cost;
                updateCoins();
                if (upgrade === 'clickValue') {
                    clickValue += 1;
                    localStorage.setItem('clickValue', clickValue);
                } else if (upgrade === 'autoClicker') {
                    startAutoClicker();
                }
                upgradePrices[upgrade] *= 2;
                localStorage.setItem('upgradePrices', JSON.stringify(upgradePrices));
                updateUpgradePrices();
            } else {
                alert('Not enough TPC!');
            }
        });
    });

    function startAutoClicker() {
        if (!autoClickerInterval) {
            autoClickerInterval = setInterval(() => {
                tpcCount += clickValue;
                updateCoins();
                saveCoins();
                createCoinEffect();
            }, 1000);
            localStorage.setItem('autoClickerRunning', 'true');
        }
    }

    // Calculate earnings while the user was away
    function calculateOfflineEarnings() {
        const lastExitTime = localStorage.getItem('lastExitTime');
        if (lastExitTime) {
            const currentTime = Date.now();
            const elapsedTime = (currentTime - parseInt(lastExitTime)) / 1000; // in seconds
            const maxOfflineTime = 3 * 60 * 60; // 3 hours in seconds

            const effectiveTime = Math.min(elapsedTime, maxOfflineTime);
            const earnings = Math.floor(effectiveTime) * clickValue;

            tpcCount += earnings;
            updateCoins();
            saveCoins();
        }
    }

    // Initial update of upgrade prices
    updateUpgradePrices();

    // Check if autoClicker was running
    if (localStorage.getItem('autoClickerRunning') === 'true') {
        startAutoClicker();
    }

    // Calculate offline earnings on page load
    calculateOfflineEarnings();

    // Save the time of exit before the page unloads
    window.addEventListener('beforeunload', () => {
        localStorage.setItem('lastExitTime', Date.now());
    });

    // Определяем задания и их требования
    const tasks = [
        { id: 'task1', description: 'Накликай 1000 раз', requirement: 1000 },
        { id: 'task2', description: 'Будь в игре 30 минут', requirement: 30 * 60 },
        { id: 'task3', description: 'Накопи 100 монет', requirement: 100 }
         // 30 минут в секундах
    ];

    // Функция для проверки выполнения задания
    function checkTaskCompletion(taskId, currentProgress) {
        const task = tasks.find(task => task.id === taskId);
        if (task && currentProgress >= task.requirement) {
            // Задание выполнено, показываем временное сообщение "success"
            const successMessage = document.createElement('div');
            successMessage.textContent = `Вы выполнили задание: ${task.description}!`;
            successMessage.classList.add('success-message');
            document.body.appendChild(successMessage);

            // Скрыть сообщение через 3 секунды (3000 миллисекунд)
            setTimeout(() => {
                document.body.removeChild(successMessage);
            }, 3000);

            // Удаляем задание из списка
            const taskIndex = tasks.findIndex(t => t.id === taskId);
            if (taskIndex !== -1) {
                tasks.splice(taskIndex, 1);
            }

            // Обновляем интерфейс
            const taskElement = document.getElementById(taskId);
            if (taskElement) {
                taskElement.parentNode.removeChild(taskElement);
            }
        }
    }

    // Обновление прогресса задания
    function updateTaskProgress(taskId, currentProgress) {
        const taskProgressElement = document.getElementById(`${taskId}-progress`);
        const taskRequirement = tasks.find(task => task.id === taskId).requirement;
        const progressPercentage = (currentProgress / taskRequirement) * 100;
        taskProgressElement.style.width = `${progressPercentage}%`;
    }

    // Добавляем слушатели для кнопки "Mine" и покупки автокликера
    const mineButton = document.getElementById('mine-button');
    mineButton.addEventListener('click', function () {
        // Обновляем прогресс задания "Накликай 1000 раз"
        const task1Progress = parseInt(localStorage.getItem('task1Progress')) || 0;
        localStorage.setItem('task1Progress', task1Progress + 1);
        updateTaskProgress('task1', task1Progress + 1);

        // Проверяем выполнение задания
        checkTaskCompletion('task1', task1Progress + 1);
    });

    const autoClickerButton = document.querySelector('.buy-button[data-upgrade="autoClicker"]');
    autoClickerButton.addEventListener('click', function () {
        // Обновляем прогресс задания "Получи автокликер"
        const task2Progress = parseInt(localStorage.getItem('task2Progress')) || 0;
        localStorage.setItem('task2Progress', 1);
        updateTaskProgress('task2', 1);

        // Проверяем выполнение задания
        checkTaskCompletion('task2', 1);

        // Запускаем таймер, если он еще не запущен
        if (!inGameTimer) {
            startTimer();
        }
    });


    // Загрузка прогресса заданий из localStorage
    tasks.forEach(task => {
        const taskProgress = parseInt(localStorage.getItem(`${task.id}Progress`)) || 0;
        updateTaskProgress(task.id, taskProgress);
        checkTaskCompletion(task.id, taskProgress);
    });

    // Добавим функцию для проверки, находится ли пользователь в игре
    let timeSpentInGame = 0; // переменная для отслеживания времени, проведенного в игре (в секундах)
    let inGameTimer; // переменная для таймера

    // Функция, которая будет вызываться каждую секунду
    function tick() {
        timeSpentInGame++; // увеличиваем время, проведенное в игре
        updateTaskProgress('task2', timeSpentInGame); // обновляем прогресс задания "Быть в игре 30 минут"

        // Проверяем выполнение задания
        checkTaskCompletion('task2', timeSpentInGame);
    }

    // Запуск таймера при загрузке страницы
    function startTimer() {
        inGameTimer = setInterval(tick, 1000); // вызываем функцию tick каждую секунду
    }

    // Запускаем таймер, когда DOM загружен
    document.addEventListener('DOMContentLoaded', startTimer);

    // Останавливаем таймер при выходе из игры
    window.addEventListener('beforeunload', () => {
        clearInterval(inGameTimer); // очищаем интервал таймера
    });

    // При обновлении страницы, сохраняем время, проведенное в игре
    window.addEventListener('beforeunload', () => {
        localStorage.setItem('timeSpentInGame', timeSpentInGame);
    });

    // При загрузке страницы, восстанавливаем время, проведенное в игре
    window.addEventListener('DOMContentLoaded', () => {
        timeSpentInGame = parseInt(localStorage.getItem('timeSpentInGame')) || 0;
        startTimer(); // запускаем таймер
    });

    // Добавляем слушатели для кнопки "Mine" и покупки автокликера
    mineButton.addEventListener('click', function () {
        // Обновляем прогресс задания "Накликай 1000 раз"
        const task1Progress = parseInt(localStorage.getItem('task1Progress')) || 0;
        localStorage.setItem('task1Progress', task1Progress + 1);
        updateTaskProgress('task1', task1Progress + 1);

        // Проверяем выполнение задания
        checkTaskCompletion('task1', task1Progress + 1);
    });

    autoClickerButton.addEventListener('click', function () {
        // Обновляем прогресс задания "Получи автокликер"
        const task3Progress = parseInt(localStorage.getItem('task3Progress')) || 0;
        localStorage.setItem('task3Progress', 1);
        updateTaskProgress('task3', 1);

        // Проверяем выполнение задания
        checkTaskCompletion('task3', 1);
    });
});
