# GeneralManager

## Überblick

Das GeneralManager-Modul ist ein leistungsstarkes und flexibles Framework, das speziell für die Verwaltung und Verarbeitung von Daten entwickelt wurde. Es bietet eine modulare Struktur, die es Entwicklern ermöglicht, komplexe Geschäftslogiken effizient zu implementieren und zu verwalten. Das Modul ist vollständig in Python geschrieben und nutzt Django als Backend-Framework.

## Hauptfunktionen

### 1. **Datenmanagement**
- **Flexibilität**: Unterstützt die Verwaltung aller Arten von Daten, nicht nur Projekte und Derivate.
- **Datenbank-Integration**: Nahtlose Integration mit dem Django ORM für Datenbankoperationen.
- **Externe Schnittstellen**: Unterstützung für Schnittstellen zu anderen Programmen, wie z. B. Excel-Interfaces.

### 2. **Datenmodellierung**
- **Django-Modelle**: Die Datenstruktur basiert auf Django-Modellen, die durch benutzerdefinierte Felder wie `MeasurementField` erweitert werden.
- **Regeln und Validierungen**: Definieren Sie Regeln für Datenvalidierungen, z. B. dass das Startdatum eines Projekts vor dem Enddatum liegen muss.

### 3. **GraphQL-Integration**
- Automatische Generierung von GraphQL-Schnittstellen für alle Modelle.
- Unterstützung für benutzerdefinierte Abfragen und Mutationen.

### 4. **Berechtigungssystem**
- **ManagerBasedPermission**: Ein flexibles Berechtigungssystem, das auf Benutzerrollen und Attributen basiert.
- Unterstützung für CRUD-Berechtigungen auf Attributebene.

### 5. **Interfaces**
- **CalculationInterface**: Ermöglicht die Implementierung von Berechnungslogiken.
- **DatabaseInterface**: Bietet eine standardisierte Schnittstelle für Datenbankoperationen.
- **ReadOnlyInterface**: Für schreibgeschützte Datenzugriffe.

### 6. **Datenverteilung und Berechnung**
- **Volumenverteilung**: Automatische Berechnung und Verteilung von Volumen über mehrere Jahre.
- **Kommerzielle Berechnungen**: Berechnung von Gesamtvolumen, Versandkosten und Einnahmen für Projekte.

## Anwendung

### Installation

Installieren Sie das Modul über `pip`:

```bash
pip install GeneralManager
```

### Beispielcode

Hier ist ein Beispiel, wie Sie einen GeneralManager erstellen und Testdaten (in diesem Fall 10 Projekte) generieren können:

```python
from general_manager import GeneralManager
from general_manager.interface.database import DatabaseInterface
from general_manager.measurement import MeasurementField, Measurement
from general_manager.permission import ManagerBasedPermission

class Project(GeneralManager):
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    total_capex: Optional[Measurement]
    derivative_list: DatabaseBucket[Derivative]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        number = CharField(max_length=7, validators=[RegexValidator(r"^AP\d{4,5}$")])
        description = TextField(null=True, blank=True)
        start_date = DateField(null=True, blank=True)
        end_date = DateField(null=True, blank=True)
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

        class Meta:
            constraints = [
                constraints.UniqueConstraint(
                    fields=["name", "number"], name="unique_booking"
                )
            ]

            rules = [
                Rule["Project"](
                    lambda x: cast(date, x.start_date) < cast(date, x.end_date)
                ),
                Rule["Project"](lambda x: cast(Measurement, x.total_capex) >= "0 EUR"),
            ]

        class Factory:
            name = LazyProjectName()
            end_date = LazyDeltaDate(365 * 6, "start_date")
            total_capex = LazyMeasurement(75_000, 1_000_000, "EUR")

    class Permission(ManagerBasedPermission):
        __read__ = ["ends_with:name:X-771", "public"]
        __create__ = ["admin", "isMatchingKeyAccount"]
        __update__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]
        __delete__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]

        total_capex = {"update": ["isSalesResponsible", "isProjectManager"]}

Project.Factory.createBatch(10)
```

### GraphQL-Integration

Das Modul generiert automatisch GraphQL-Schnittstellen für alle Modelle. Sie können Abfragen und Mutationen über die GraphQL-URL ausführen, die in den Django-Einstellungen definiert ist.

Beispiel für eine GraphQL-Abfrage:

```graphql
query {
  projectList {
    name
    startDate
    endDate
    totalCapex {
      value
      unit
    }
  }
}
```

## Vorteile

- **Modularität**: Einfach erweiterbar und anpassbar.
- **Flexibilität**: Unterstützt komplexe Geschäftslogiken und Berechnungen.
- **Integration**: Nahtlose Integration mit Django und GraphQL.
- **Berechtigungen**: Fein abgestimmte Berechtigungen für Benutzer und Attribute.
- **Datenvalidierung**: Automatische Validierung von Daten durch Regeln und Constraints.
- **Caching**: Automatische Cache-Generierung mit @cached Decorator, um die Leistung zu verbessern.

## Anforderungen

- Python >= 3.12
- Django >= 5.2
- Zusätzliche Abhängigkeiten (siehe `requirements.txt`):
  - `graphene`
  - `numpy`
  - `Pint`
  - `factory_boy`
  - uvm.

## Lizenz

Dieses Projekt steht unter der **Non-Commercial MIT License**. Es darf nur für nicht-kommerzielle Zwecke verwendet werden. Weitere Details finden Sie in der [LICENSE](./LICENSE).
