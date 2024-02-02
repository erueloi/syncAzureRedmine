# Script de Sincronización de Azure a Redmine

## Descripción
Este script sincroniza tareas entre Azure y Redmine. Está diseñado para ejecutarse como una herramienta de línea de comandos, aceptando parámetros para números de sprint específicos, y utiliza llamadas API para obtener y actualizar tareas en ambos sistemas.

## Requisitos
- Python 3.x
- Paquetes de Pip: `unidecode`, `requests`, `redminelib`, `python-dotenv`, `argparse`, `pdfkit`, `smtplib`, `email`

## Instrucciones de Configuración
1. Clona el repositorio y navega al directorio del proyecto.
2. Instala los paquetes de Python requeridos:
   
   pip install unidecode requests redminelib python-dotenv argparse pdfkit smtplib email
4. Crea un archivo `.env` en la raíz del proyecto con las siguientes variables de entorno:

    AZURE_DEVOPS_PROJECT_BASE=<tu_url_base_del_proyecto_de_azure_devops>  
    AZURE_TEAM=<tu_nombre_de_equipo_de_azure>  
    AZURE_TOKEN=<tu_token_de_acceso_de_azure_devops>  
    AREA_PATH=<tu_area_path>  
    ITERATION_PATH=<tu_iteration_path>  
    REDMINE_URL=<tu_url_de_redmine>  
    REDMINE_TOKEN=<tu_token_de_acceso_de_redmine>  
    PROJECT_ID=<tu_id_del_proyecto>  
    ID_CAMPO_HORAS_RESTANTES=<tu_id_del_campo_horas_restantes>  
    ID_CAMPO_IBER_IDCLIENTE=<tu_id_del_campo_iber_idcliente>
    ...

4. Ajusta la configuración de registro en el script según sea necesario.

## Uso
Ejecuta el script utilizando la línea de comandos:

python azure_to_redmine_sync.py <número_del_sprint>

## Resultados Esperados
Tras una ejecución exitosa, el script sincronizará las tareas entre Azure y Redmine basándose en el número de sprint especificado. Registra operaciones, envía notificaciones por correo electrónico si está configurado y actualiza tareas en ambos sistemas.

## Contribuciones
Las contribuciones son bienvenidas. Por favor, bifurca el repositorio y envía una solicitud de extracción con tus cambios.

## Licencia
[Inserta aquí la información de tu licencia]
