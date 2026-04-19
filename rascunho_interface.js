<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Coluna Config & Badges</title>
    <style>
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #ccc; padding: 10px; text-align: left; }
        
        /* Essencial para o badge absoluto */
        .col-header { 
            position: relative; 
            cursor: pointer; 
            user-select: none;
        }

        /* Estilo do Badge */
        .badge {
            position: absolute;
            top: -5px;
            right: -5px;
            background-color: red;
            color: white;
            font-size: 11px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 10px;
            pointer-events: none; /* Evita que o badge atrapalhe o clique no header */
        }
    </style>
</head>
<body>

    <button onclick="simularNovoAlerta()">Simular Alerta em Coluna Aleatória</button>
    
    <table id="dataTable">
        <thead>
            <tr id="headerRow">
                <!-- Colunas serão geradas via JS para demonstrar a restauração -->
            </tr>
        </thead>
        <tbody>
            <tr><td>Dados de exemplo...</td><td>...</td><td>...</td></tr>
        </tbody>
    </table>

    <script>
        // Configurações iniciais
        const STORAGE_KEY = 'table_col_config';
        const defaultColumns = [
            { query: 'SELECT name...', name: 'Nome', sort: 'asc' },
            { query: 'SELECT age...', name: 'Idade', sort: 'desc' },
            { query: 'SELECT city...', name: 'Cidade', sort: 'asc' }
        ];

        /**
         * 1. Salva no localStorage o objeto de N colunas
         */
        function saveColumnConfig(config) {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
        }

        /**
         * 2. Restaura as configurações ao carregar a página
         */
        function initTable() {
            const saved = localStorage.getItem(STORAGE_KEY);
            const columns = saved ? JSON.parse(saved) : defaultColumns;
            
            // Salva as default caso não existam
            if (!saved) saveColumnConfig(columns);

            const headerRow = document.getElementById('headerRow');
            headerRow.innerHTML = ''; // Limpa

            columns.forEach((col, index) => {
                const th = document.createElement('th');
                th.className = 'col-header';
                th.dataset.id = index;
                th.innerText = col.name;

                // Evento para remover o badge ao clicar
                th.addEventListener('click', function() {
                    const badge = this.querySelector('.badge');
                    if (badge) badge.remove();
                });

                headerRow.appendChild(th);
            });
        }

        /**
         * 3. Cria o Badge de contador
         */
        function addBadge(columnId, count) {
            const header = document.querySelector(`.col-header[data-id="${columnId}"]`);
            if (!header) return;

            // Verifica se já existe um badge para somar ou criar novo
            let badge = header.querySelector('.badge');
            if (badge) {
                let currentCount = parseInt(badge.innerText);
                badge.innerText = currentCount + count;
            } else {
                badge = document.createElement('div');
                badge.className = 'badge';
                badge.innerText = count;
                header.appendChild(badge);
            }
        }

        // Função auxiliar para testar os badges
        function simularNovoAlerta() {
            const colCount = document.querySelectorAll('.col-header').length;
            const randomCol = Math.floor(Math.random() * colCount);
            addBadge(randomCol, 1);
        }

        // Executa ao carregar a página
        window.onload = initTable;
    </script>
</body>
</html>